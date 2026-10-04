"""Point the live Render service at the private backup repository.

Free/plain container hosting keeps SQLite inside the container, so a spin-down, a
redeploy or a crash starts from an empty database - that is how a work "disappears".
`app/backup.py` archives the whole data directory to a private GitHub repository and
restores it on boot; this script sets the two environment variables that switch it on.

    RENDER_API_KEY=rnd_... python3 -m tools.set_backup_env \
        --service srv-davab4qa3nsc73fgbe90 \
        --repo mukesh931/smart-mb-data \
        --token ghp_...            # a GitHub PAT with contents:write on that repo

    python3 -m tools.set_backup_env --show          # just look at the current values

The API key and the token are only sent to Render/GitHub; nothing is written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

RENDER_API = "https://api.render.com/v1"


def request(method: str, path: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(RENDER_API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Render API {method} {path} -> {exc.code}: {exc.read()[:400].decode(errors='replace')}")


def service_env(key: str, service: str) -> dict:
    out = request("GET", f"/services/{service}/env-vars?limit=100", key)
    rows = out if isinstance(out, list) else out.get("envVars", [])
    env = {}
    for row in rows:
        item = row.get("envVar") or row
        env[item.get("key")] = item.get("value")
    return env


def main() -> None:
    ap = argparse.ArgumentParser(description="Configure Smart-MB backups on Render")
    ap.add_argument("--service", default=os.environ.get("RENDER_SERVICE", "srv-davab4qa3nsc73fgbe90"))
    ap.add_argument("--repo", default=os.environ.get("SMARTMB_BACKUP_REPO", "mukesh931/smart-mb-data"))
    ap.add_argument("--token", default=os.environ.get("SMARTMB_BACKUP_TOKEN", ""))
    ap.add_argument("--path", default="snapshots/smartmb-data.tar.gz")
    ap.add_argument("--interval", default="120")
    ap.add_argument("--show", action="store_true", help="print the current environment and exit")
    args = ap.parse_args()

    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        sys.exit("RENDER_API_KEY is not set (Render dashboard -> Account settings -> API keys).")

    current = service_env(key, args.service)
    interesting = {k: v for k, v in current.items() if k.startswith("SMARTMB")}
    print(f"service {args.service}: {len(current)} env var(s)")
    for k, v in sorted(interesting.items()):
        shown = v if not v or k.endswith("TOKEN") is False else (v[:6] + "…" if v else v)
        print(f"  {k} = {shown}")
    if args.show:
        return

    if not args.token:
        sys.exit("--token (or SMARTMB_BACKUP_TOKEN) is required to enable backups.")

    wanted = {
        "SMARTMB_BACKUP_REPO": args.repo,
        "SMARTMB_BACKUP_TOKEN": args.token,
        "SMARTMB_BACKUP_PATH": args.path,
        "SMARTMB_BACKUP_INTERVAL": str(args.interval),
    }
    payload = [{"key": k, "value": v} for k, v in wanted.items()]
    request("PUT", f"/services/{args.service}/env-vars", key, payload)
    print("env vars written:")
    for k in wanted:
        print(f"  {k} = {'(set)' if 'TOKEN' in k else wanted[k]}")
    print("Render redeploys the service automatically; the next boot restores the latest snapshot.")


if __name__ == "__main__":
    main()
