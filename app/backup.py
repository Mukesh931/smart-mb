"""Durable storage for an instance whose disk is not durable.

Render's free plan (and any container that restarts) throws away everything written to
the filesystem: the SQLite database, uploaded estimates and site photos.  Engineers
lost a freshly created work that way, mid-visit.

This module keeps a copy of the whole data directory (database + uploads + photos) in a
private GitHub repository and puts it back when the container comes up empty:

* `restore_if_empty()` runs at start-up, before the database is created.  If the data
  directory has no database yet and a snapshot exists in the repository, it is unpacked.
* `mark_dirty()` is called from the request path; a background thread pushes a snapshot
  at most every `SMARTMB_BACKUP_INTERVAL` seconds (default 120) and once more on SIGTERM.
* `status()`, `snapshot_now()` and `restore_latest()` back the admin UI, so an
  administrator can also download a copy or restore one by hand — no token required.

Configuration (all optional; without a token the sync is simply disabled and the admin
UI still offers manual download/restore):

    SMARTMB_BACKUP_REPO     owner/name of a (private) repository
    SMARTMB_BACKUP_TOKEN    a fine-grained PAT with contents:write on that repository
    SMARTMB_BACKUP_PATH     path inside the repo, default snapshots/smartmb-data.tar.gz
    SMARTMB_BACKUP_INTERVAL seconds between automatic pushes, default 120
"""
from __future__ import annotations

import base64
import io
import json
import os
import tarfile
import threading
import time
import urllib.error
import urllib.request

from .db import DATA_DIR, DB_PATH

REPO = os.environ.get("SMARTMB_BACKUP_REPO", "").strip()
TOKEN = os.environ.get("SMARTMB_BACKUP_TOKEN", "").strip()
SNAP_PATH = os.environ.get("SMARTMB_BACKUP_PATH", "snapshots/smartmb-data.tar.gz").strip()
INTERVAL = int(os.environ.get("SMARTMB_BACKUP_INTERVAL", "120") or 120)
API = "https://api.github.com"

_lock = threading.Lock()
_state = {"last_push": None, "last_error": None, "last_size": 0,
          "last_restore": None, "dirty": False, "thread": None}


def configured() -> bool:
    return bool(REPO and TOKEN)


def status(enabled_only: bool = False) -> dict:
    if enabled_only and not configured():
        return {"configured": False}
    return {"configured": configured(), "repo": REPO, "path": SNAP_PATH, "interval": INTERVAL,
            "last_push": _state["last_push"], "last_size": _state["last_size"],
            "last_restore": _state["last_restore"], "last_error": _state["last_error"],
            "pending": _state["dirty"]}


# ------------------------------------------------------------------ snapshots
def make_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("smartmb.sqlite3", "uploads", "photos"):
            full = os.path.join(DATA_DIR, name)
            if os.path.exists(full):
                tar.add(full, arcname=name)
    return buf.getvalue()


def unpack_archive(blob: bytes) -> dict:
    """Unpack a snapshot over the data directory (database first, files after)."""
    restored = {"db": False, "files": 0}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name in ("smartmb.sqlite3", "uploads", "photos")
                   or m.name.startswith(("uploads/", "photos/"))]
        for m in members:
            if ".." in m.name or m.name.startswith("/"):
                continue
            tar.extract(m, path=DATA_DIR, filter="data")
            if m.name == "smartmb.sqlite3":
                restored["db"] = True
            elif m.isfile():
                restored["files"] += 1
    _state["last_restore"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return restored


def restore_if_empty() -> dict | None:
    """Called before the database is opened: on a fresh container put the last
    snapshot back, so a restart no longer means an empty platform."""
    if os.path.exists(DB_PATH) or not configured():
        return None
    try:
        blob = _download()
        if not blob:
            return None
        info = unpack_archive(blob)
        info["source"] = f"{REPO}/{SNAP_PATH}"
        return info
    except Exception as exc:                                     # pragma: no cover - network
        _state["last_error"] = f"restore failed: {exc}"
        return None


# ------------------------------------------------------------------ github transport
def _req(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
        "User-Agent": "smart-mb-backup", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body) if body else {}


def _download() -> bytes | None:
    try:
        meta = _req("GET", f"{API}/repos/{REPO}/contents/{SNAP_PATH}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if meta.get("content"):
        return base64.b64decode(meta["content"])
    return None


def _upload(blob: bytes, message: str) -> dict:
    """Push the snapshot with the Git Data API (works for files well over 1 MB)."""
    ref = _req("GET", f"{API}/repos/{REPO}/git/ref/heads/main")["object"]["sha"]
    base = _req("GET", f"{API}/repos/{REPO}/git/commits/{ref}")["tree"]["sha"]
    parts = SNAP_PATH.split("/")
    # blobs → tree for the snapshot's folder, grafted onto the existing root tree
    blob_sha = _req("POST", f"{API}/repos/{REPO}/git/blobs",
                    {"content": base64.b64encode(blob).decode(), "encoding": "base64"})["sha"]
    tree: dict = {"path": SNAP_PATH, "mode": "100644", "type": "blob", "sha": blob_sha}
    for i in range(len(parts) - 1, 0, -1):
        tree = {"path": parts[i - 1], "mode": "040000", "type": "tree", "content": [tree]}
    new_tree = _req("POST", f"{API}/repos/{REPO}/git/trees",
                    {"base_tree": base, "tree": [tree]})["sha"]
    commit = _req("POST", f"{API}/repos/{REPO}/git/commits",
                  {"message": message, "tree": new_tree, "parents": [ref]})["sha"]
    _req("PATCH", f"{API}/repos/{REPO}/git/refs/heads/main", {"sha": commit, "force": True})
    return {"commit": commit, "bytes": len(blob)}


def snapshot_now(reason: str = "manual") -> dict:
    if not configured():
        raise RuntimeError("Backup is not configured - set SMARTMB_BACKUP_REPO and SMARTMB_BACKUP_TOKEN")
    with _lock:
        blob = make_archive()
        out = _upload(blob, f"Smart-MB data snapshot ({reason})")
        _state.update(last_push=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      last_size=len(blob), last_error=None, dirty=False)
        return out


def restore_latest() -> dict:
    blob = _download()
    if not blob:
        raise RuntimeError("No snapshot found in the backup repository")
    return unpack_archive(blob)


# ------------------------------------------------------------------ request-path hooks
def mark_dirty() -> None:
    _state["dirty"] = True


def _loop() -> None:
    while True:
        time.sleep(10)
        if not _state["dirty"]:
            continue
        last = _state["last_push"]
        if last and time.time() - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ")) + 1 < INTERVAL:
            continue
        try:
            snapshot_now("auto")
        except Exception as exc:                                 # pragma: no cover - network
            _state["last_error"] = str(exc)
            _state["dirty"] = False      # do not hammer GitHub; the next write retries


def start_background() -> None:
    if not configured() or _state["thread"]:
        return
    t = threading.Thread(target=_loop, daemon=True, name="smartmb-backup")
    t.start()
    _state["thread"] = t
