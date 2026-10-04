"""Embeds a read-only JSON snapshot of the demo dataset into web/index.html so the
app renders inside sandboxed previews that have no network access.

Run after seeding:  python3 -m tools.embed_demo
"""
from __future__ import annotations
import json, os, re
from app import db, main, schedule_store, seed
from app.db import rows_to_dicts

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(BASE, "web", "index.html")


def build_snapshot() -> dict:
    db.init_db(); seed.ensure_seed()
    admin = dict(db.q1("SELECT * FROM users WHERE role='admin' LIMIT 1"))
    admin.pop("password_hash", None)
    project_id = db.q1("SELECT id FROM projects LIMIT 1")["id"]
    p = main.project_stats(project_id)
    p["project"] = dict(p["project"])
    p["team"] = {"name": p["project"].get("engineer_id") and db.q1(
        "SELECT name, designation FROM users WHERE id=?", (p["project"]["engineer_id"],))["name"] or ""}
    p["parse_jobs"] = []
    cl = main.checklist(project_id, None, admin)
    dev = main.deviations(project_id, admin)
    items = main.project_items(project_id, admin)["items"]
    meas = main.list_measurements(project_id, None, admin)["measurements"][:40]
    verify = schedule_store.verify_worklist(project_id)
    verify["docs"] = [{k: v for k, v in d.items() if k != "raw_json"}
                      for d in schedule_store.list_documents(project_id)]
    recon = schedule_store.reconciliation(project_id)
    recon["rows"] = [{k: v for k, v in r.items() if k != "cells"} for r in recon["rows"]][:60]
    schedule_doc = None
    _docs = schedule_store.list_documents(project_id)
    if _docs:
        schedule_doc = schedule_store.get_document(_docs[0]["id"])
        schedule_doc["cells"] = schedule_doc["cells"][:120]
        # the linking sheet only needs the column headings + their candidate shortlists
        try:
            _raw = json.loads(schedule_doc.pop("raw_json") or "{}")
            schedule_doc["raw_json"] = json.dumps({"columns": [
                {"order": c.get("order"), "label": c.get("label"), "cells": {},
                 "match_confidence": c.get("match_confidence"), "match_ambiguous": c.get("match_ambiguous"),
                 "alternatives": (c.get("alternatives") or [])[:3]} for c in _raw.get("columns", [])]})
        except Exception:
            schedule_doc["raw_json"] = "{}"
    versions = rows_to_dicts(db.q("SELECT * FROM csr_versions ORDER BY fy DESC, region"))
    for v in versions:
        v["live_count"] = v["item_count"]
    fy, region = versions[0]["fy"], versions[0]["region"]
    it_rows = rows_to_dicts(db.q(
        "SELECT * FROM master_items WHERE fy=? AND region=? ORDER BY chapter, item_code LIMIT 120", (fy, region)))
    for r in it_rows:
        r["tags"] = json.loads(r.get("tags") or "[]")
    facets = main.csr_facets(fy, region, admin)
    dash = main.dashboard(admin)
    overview = main.admin_overview(admin)
    overview["stats"]["items_per_version"] = [
        {k: v for k, v in x.items() if k in ("fy", "region", "item_count", "status", "source_file", "uploaded_at")}
        for x in overview["stats"]["items_per_version"]]
    return {
        "user": admin,
        "org": main.org_tree(admin),
        "backup": {"configured": False, "repo": "(demo)", "storage": "ephemeral", "last_push": None,
                   "last_restore": None, "pending": False, "db_bytes": 0, "data_dir": "(demo snapshot)",
                   "path": "", "interval": 0},
        "versions": versions,
        "facets": facets,
        "csr_items": {"total": len(it_rows), "items": it_rows, "limit": 120, "offset": 0},
        "suggest": it_rows[:8],
        "projects": main.list_projects(admin)["projects"],
        "project": p,
        "items": items,
        "checklist": cl,
        "deviations": dev,
        "measurements": meas,
        "verify": verify,
        "reconciliation": recon,
        "schedule_doc": schedule_doc,
        "dashboard": dash,
        "admin": overview,
    }


def main_() -> None:
    snap = build_snapshot()
    raw = json.dumps(snap, default=str)
    html = open(HTML, encoding="utf-8").read()
    html = re.sub(r'(<script id="demo-snapshot" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + raw.replace("</", "<\\/") + m.group(2), html, flags=re.S)
    open(HTML, "w", encoding="utf-8").write(html)
    print(f"embedded snapshot: {len(raw)/1024:.1f} KiB -> {HTML}")
    # the asset version stamp must move whenever the bundle does, or phones keep the
    # old app.js — run the stamper as the last step of every build
    try:
        from . import stamp_assets
    except ImportError:                                          # direct script use
        import stamp_assets                                      # type: ignore
    print(f"asset stamp: {stamp_assets.stamp()}")


if __name__ == "__main__":
    main_()

