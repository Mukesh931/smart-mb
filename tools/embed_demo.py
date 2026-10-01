"""Embeds a read-only JSON snapshot of the demo dataset into web/index.html so the
app renders inside sandboxed previews that have no network access.

Run after seeding:  python3 -m tools.embed_demo
"""
from __future__ import annotations
import json, os, re
from app import db, main, seed
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


if __name__ == "__main__":
    main_()
