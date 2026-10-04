"""
Smart-MB :: Descriptive Schedule store + room-wise reconciliation.

Bridges the parsed schedule (app/schedule.py) to the project:
  * save a parsed document (columns = work items, rows = locations)
  * materialise the schedule locations as project rooms (fuzzy-matched to existing ones)
  * reconcile the schedule against the estimate: per item, per location
  * record the engineer's on-site verification: **keep** (as per schedule) or
    **change to actual** - a "change" also writes a measurement so that Form-23,
    the deviations sheet and the dashboard all move together.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import schedule as sch
from .db import audit, ex, json_load, now_iso, q, q1, rows_to_dicts


# --------------------------------------------------------------------- helpers
AUTO_LINK_MIN = 0.5        # below this the column is left for the engineer to link by hand


def _project(pid: int) -> dict:
    from fastapi import HTTPException
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    return dict(p)


def _master_index(fy: str, region: str) -> list[dict]:
    return rows_to_dicts(q("""SELECT id, item_code, short_desc, description, unit, rate, category, tags
                              FROM master_items WHERE fy=? AND region=? AND is_active=1
                              ORDER BY chapter, section, item_code""", (fy, region)))


def project_items(pid: int) -> list[dict]:
    return rows_to_dicts(q("""SELECT id, item_code, description, unit, rate, tendered_qty, is_non_schedule,
                                     master_item_id, sort_order
                              FROM project_items WHERE project_id=? ORDER BY sort_order, id""", (pid,)))


def _normalise_label(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def match_room(project_id: int, label: str, used: set[int] | None = None) -> tuple[int | None, float]:
    """Find the project room that corresponds to a schedule location.

    Exact (normalised) names always win; fuzzy matches are accepted only above a high
    threshold so that HALL / MAIN HALL or TOILET / TOILET PASSAGE stay separate rooms.
    A room already claimed by another location of the same schedule is never reused."""
    used = used or set()
    norm = _normalise_label(label)
    best_id, best_score = None, 0.0
    for room in rows_to_dicts(q("SELECT id, name FROM rooms WHERE project_id=?", (project_id,))):
        if room["id"] in used:
            continue
        if _normalise_label(room["name"]) == norm and norm:
            return room["id"], 1.0
        score = sch.similarity(label, room["name"])
        if score > best_score:
            best_id, best_score = room["id"], score
    if best_score >= 0.92:
        return best_id, round(best_score, 3)
    return None, round(best_score, 3)


def ensure_room(project_id: int, label: str, floor: str = "", sort_order: int = 0,
                used: set[int] | None = None) -> tuple[int, float]:
    """Return (room_id, match_score), creating a room when the schedule names a new location."""
    rid, score = match_room(project_id, label, used)
    if rid:
        return rid, score
    existing = {_normalise_label(r["name"]) for r in
                rows_to_dicts(q("SELECT name FROM rooms WHERE project_id=?", (project_id,)))}
    name = label
    if _normalise_label(name) in existing and _normalise_label(name):
        n = 2
        while _normalise_label(f"{label} ({n})") in existing:
            n += 1
        name = f"{label} ({n})"
    ts = now_iso()
    rid = ex("""INSERT INTO rooms (project_id, floor, name, sort_order, created_at) VALUES (?,?,?,?,?)""",
             (project_id, floor or "", name, sort_order, ts)).lastrowid
    return rid, 1.0


# ------------------------------------------------------------------ save / read
def save_document(project_id: int, filename: str, parsed: dict, user: dict | None,
                  column_map: dict[int, dict] | None = None) -> dict:
    """Persist a parsed descriptive schedule + attach every column to a project item."""
    p = _project(project_id)
    items = project_items(project_id)
    master = _master_index(p["csr_fy"], p["csr_region"])

    cols = parsed.get("columns", [])
    proposed = sch.map_columns(cols, items, master)
    ts = now_iso()
    doc_id = ex("""INSERT INTO schedule_docs (project_id, filename, engine, orientation, title, name_of_work,
                    estimate_no, stats, warnings, raw_json, status, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?, 'imported', ?, ?)""",
                (project_id, filename, parsed.get("engine", ""), parsed.get("orientation", ""),
                 parsed.get("title", "Descriptive Schedule"), parsed.get("name_of_work", ""),
                 parsed.get("estimate_no", ""), json.dumps(parsed.get("stats", {})),
                 json.dumps(parsed.get("warnings", [])), json.dumps(parsed), user["id"] if user else None, ts)
                ).lastrowid

    # locations -> rooms
    loc_ids: dict[int, tuple[int, int]] = {}      # parsed order -> (location_id, room_id)
    claimed: set[int] = set()
    for loc in parsed.get("locations", []):
        rid, score = ensure_room(project_id, loc["label"], loc.get("floor", ""), int(loc.get("order", 0)),
                                 used=claimed)
        claimed.add(rid)
        lid = ex("""INSERT INTO schedule_locations (doc_id, project_id, floor, name, sort_order, row_total,
                       room_id, match_score, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (doc_id, project_id, loc.get("floor", ""), loc["label"], int(loc.get("order", 0)),
                  float(loc.get("row_total") or 0), rid, score, ts)).lastrowid
        loc_ids[int(loc.get("order", 0))] = (lid, rid)

    # columns -> cells
    overrides = column_map or {}
    mapped = 0
    needs_link: list[dict] = []
    for col in proposed:
        corder = int(col.get("order", 0))
        override = overrides.get(corder) or overrides.get(str(corder)) or {}
        explicit = bool(override.get("project_item_id") or override.get("master_item_id")
                        or override.get("create_item"))
        weak = (float(col.get("match_confidence") or 0) < AUTO_LINK_MIN
                or bool(col.get("match_ambiguous"))) and not explicit
        pid_item = None if weak else override.get("project_item_id", col.get("suggested_project_item_id"))
        mid = None if weak else override.get("master_item_id", col.get("suggested_master_item_id"))
        item_code = override.get("item_code") or col.get("suggested_item_code")
        if override.get("create_item"):
            pid_item = _create_extra_item(project_id, override, user)
            mid, item_code = None, override.get("item_code")
        if pid_item:
            row = q1("SELECT * FROM project_items WHERE id=? AND project_id=?", (pid_item, project_id))
            if row:
                mid = row["master_item_id"] or mid
                item_code = row["item_code"] or item_code
                mapped += 1
        if weak and col.get("cells"):
            needs_link.append({"column_order": corder, "label": col["label"],
                               "reason": "ambiguous" if col.get("match_ambiguous") else "low_confidence",
                               "suggested_item_code": col.get("suggested_item_code"),
                               "suggested_project_item_id": col.get("suggested_project_item_id"),
                               "suggested_master_item_id": col.get("suggested_master_item_id"),
                               "confidence": col.get("match_confidence"),
                               "qty_total": round(sum(col["cells"].values()), 3)})
        cells = col.get("cells", {})
        for loc_order_raw, qty in cells.items():
            pair = loc_ids.get(int(loc_order_raw))
            if not pair:
                continue
            lid, _rid = pair
            ex("""INSERT OR REPLACE INTO schedule_cells (doc_id, project_id, location_id, column_order,
                    col_label, qty, project_item_id, master_item_id, item_code, match_confidence,
                    match_method, verify_status) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
               (doc_id, project_id, lid, corder, col["label"], float(qty), pid_item, mid, item_code,
                float(override.get("confidence") or col.get("match_confidence") or 0),
                override.get("method") or col.get("match_method") or ""))

    if user:
        audit(user, "SCHEDULE_IMPORTED", "schedule_docs", doc_id,
              {"project": project_id, "file": filename, "engine": parsed.get("engine"),
               "locations": len(parsed.get("locations", [])), "columns": len(cols), "auto_mapped": mapped})
    return {"doc_id": doc_id, "locations": len(loc_ids), "columns": len(cols),
            "auto_mapped": mapped, "unmapped": len(cols) - mapped,
            "need_link": sorted(needs_link, key=lambda r: -r["qty_total"])}


def list_documents(project_id: int) -> list[dict]:
    docs = rows_to_dicts(q("""SELECT d.*, u.name AS uploaded_by_name,
                                     (SELECT COUNT(*) FROM schedule_locations l WHERE l.doc_id=d.id) AS locations,
                                     (SELECT COUNT(*) FROM schedule_cells c WHERE c.doc_id=d.id) AS cells,
                                     (SELECT COUNT(*) FROM schedule_cells c WHERE c.doc_id=d.id
                                              AND c.verify_status IN ('kept','changed')) AS checked,
                                     (SELECT COUNT(*) FROM schedule_cells c WHERE c.doc_id=d.id
                                              AND c.verify_status='changed') AS changed
                              FROM schedule_docs d LEFT JOIN users u ON u.id=d.created_by
                              WHERE d.project_id=? ORDER BY d.id DESC""", (project_id,)))
    for d in docs:
        d["stats"] = json_load(d.get("stats"), {})
        d["warnings"] = json_load(d.get("warnings"), [])
        d["progress_pct"] = round(100.0 * (d["checked"] or 0) / (d["cells"] or 1), 1)
    return docs


def get_document(doc_id: int) -> dict:
    from fastapi import HTTPException
    doc = q1("""SELECT d.*, p.name AS project_name, p.csr_fy, p.csr_region
                FROM schedule_docs d JOIN projects p ON p.id=d.project_id WHERE d.id=?""", (doc_id,))
    if not doc:
        raise HTTPException(404, "Schedule document not found")
    doc = dict(doc)
    doc["stats"] = json_load(doc.get("stats"), {})
    doc["warnings"] = json_load(doc.get("warnings"), [])
    locations = rows_to_dicts(q("""SELECT l.*, r.name AS room_name,
                                          (SELECT COUNT(*) FROM schedule_cells c WHERE c.location_id=l.id) AS cells,
                                          (SELECT COUNT(*) FROM schedule_cells c WHERE c.location_id=l.id
                                                 AND c.verify_status IN ('kept','changed')) AS checked
                                   FROM schedule_locations l LEFT JOIN rooms r ON r.id=l.room_id
                                   WHERE l.doc_id=? ORDER BY l.sort_order, l.id""", (doc_id,)))
    cells = rows_to_dicts(q("""SELECT c.*, pi.unit, pi.rate, pi.description AS item_description,
                                      pi.tendered_qty, m.measured_qty AS measurement_qty
                               FROM schedule_cells c
                               LEFT JOIN project_items pi ON pi.id=c.project_item_id
                               LEFT JOIN measurements m ON m.id=c.measurement_id
                               WHERE c.doc_id=? ORDER BY c.column_order, c.location_id""", (doc_id,)))
    doc["locations"] = locations
    doc["cells"] = cells
    return doc


def delete_document(doc_id: int, user: dict | None) -> None:
    from fastapi import HTTPException
    doc = q1("SELECT * FROM schedule_docs WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Schedule document not found")
    ex("DELETE FROM schedule_docs WHERE id=?", (doc_id,))
    if user:
        audit(user, "SCHEDULE_DELETED", "schedule_docs", doc_id, {"project": doc["project_id"]})


# ------------------------------------------------------------------ reconcile
def reconciliation(project_id: int, doc_id: int | None = None) -> dict:
    """Item-wise reconciliation: estimate (tendered) vs descriptive schedule vs actual measured."""
    _project(project_id)
    docs = [d for d in list_documents(project_id) if doc_id is None or d["id"] == doc_id]
    items = project_items(project_id)
    by_item: dict[int, dict] = {}
    for it in items:
        by_item[it["id"]] = {
            "project_item_id": it["id"], "item_code": it["item_code"], "description": it["description"],
            "unit": it["unit"], "rate": it["rate"], "tendered_qty": it["tendered_qty"] or 0,
            "is_non_schedule": bool(it["is_non_schedule"]),
            "schedule_qty": 0.0, "locations": [], "scheduled_locations": 0,
            "linked": it["master_item_id"] is not None,
        }
    orphans: list[dict] = []
    for d in docs:
        cells = rows_to_dicts(q("""SELECT c.*, l.name AS location, l.floor AS floor_name, l.room_id AS room_id,
                                          m.measured_qty AS measured_qty
                                   FROM schedule_cells c JOIN schedule_locations l ON l.id=c.location_id
                                   LEFT JOIN measurements m ON m.id=c.measurement_id
                                   WHERE c.doc_id=? ORDER BY c.column_order, l.sort_order""", (d["id"],)))
        measured_here = {r["project_item_id"]: r["qty"] for r in rows_to_dicts(q("""
            SELECT project_item_id, COALESCE(SUM(measured_qty),0) AS qty FROM measurements
            WHERE project_id=? GROUP BY project_item_id""", (project_id,)))}
        for c in cells:
            entry = by_item.get(c["project_item_id"]) if c["project_item_id"] else None
            if entry is None:
                orphans.append({"cell_id": c["id"], "col_label": c["col_label"], "location": c["location"],
                                "qty": c["qty"], "doc_id": d["id"], "column_order": c["column_order"]})
                continue
            entry["schedule_qty"] = round(entry["schedule_qty"] + (c["qty"] or 0), 3)
            entry["scheduled_locations"] += 1
            entry["locations"].append({
                "cell_id": c["id"], "location_id": c["location_id"], "location": c["location"],
                "floor": c["floor_name"], "room_id": c["room_id"], "schedule_qty": c["qty"],
                "verify_status": c["verify_status"], "actual_qty": c["actual_qty"],
                "measured_qty": c["measured_qty"], "note": c["note"],
            })
        for it in items:
            it["measured_qty"] = round(measured_here.get(it["id"], 0.0), 3)

    rows = []
    for entry in by_item.values():
        measured = round(sum(l["measured_qty"] or 0 for l in entry["locations"]), 3)
        if not entry["locations"]:                       # no schedule grid -> item-level measurement total
            measured = round(q1("""SELECT COALESCE(SUM(measured_qty),0) AS q FROM measurements
                                   WHERE project_id=? AND project_item_id=?""",
                                (project_id, entry["project_item_id"]))["q"], 3)
        entry["measured_qty"] = measured
        entry["schedule_vs_tendered"] = round(entry["schedule_qty"] - entry["tendered_qty"], 3)
        entry["measured_vs_schedule"] = round(measured - entry["schedule_qty"], 3)
        entry["pending_cells"] = sum(1 for l in entry["locations"] if l["verify_status"] == "pending")
        entry["changed_cells"] = sum(1 for l in entry["locations"] if l["verify_status"] == "changed")
        entry["amount_schedule"] = round(entry["schedule_qty"] * (entry["rate"] or 0), 2)
        entry["amount_tendered"] = round(entry["tendered_qty"] * (entry["rate"] or 0), 2)
        rows.append(entry)
    rows.sort(key=lambda r: (-(r["pending_cells"] > 0), r["item_code"]))
    return {
        "project_id": project_id, "docs": docs, "rows": rows, "orphans": orphans,
        "totals": {
            "items": len(rows),
            "scheduled_items": sum(1 for r in rows if r["locations"]),
            "pending_cells": sum(r["pending_cells"] for r in rows),
            "changed_cells": sum(r["changed_cells"] for r in rows),
            "schedule_amount": round(sum(r["amount_schedule"] for r in rows), 2),
            "tendered_amount": round(sum(r["amount_tendered"] for r in rows), 2),
            "unmapped_columns": len({(o["doc_id"], o["column_order"]) for o in orphans}),
        },
    }


# --------------------------------------------------------------------- verify
def verify_worklist(project_id: int, doc_id: int | None = None, room_id: int | None = None,
                    only_pending: bool = False) -> dict:
    """Room-major worklist for the site phone screen: what the schedule says, what is measured."""
    _project(project_id)
    docs = [d for d in list_documents(project_id) if doc_id is None or d["id"] == doc_id]
    if not docs:
        return {"project_id": project_id, "rooms": [], "items": [], "docs": [],
                "totals": {"locations": 0, "cells": 0, "pending": 0, "changed": 0, "kept": 0}}
    doc_ids = [d["id"] for d in docs]
    mark = ",".join("?" * len(doc_ids))
    cells = rows_to_dicts(q(f"""SELECT c.*, l.name AS location, l.floor AS floor_name, l.room_id AS room_id,
                                       l.sort_order AS loc_order, pi.unit, pi.rate, pi.description AS item_description,
                                       pi.tendered_qty, pi.is_non_schedule
                                FROM schedule_cells c JOIN schedule_locations l ON l.id=c.location_id
                                LEFT JOIN project_items pi ON pi.id=c.project_item_id
                                WHERE c.doc_id IN ({mark}) ORDER BY l.sort_order, c.column_order""",
                            tuple(doc_ids)))
    rooms: dict[Any, dict] = {}
    for c in cells:
        key = c["room_id"] or f"loc-{c['location_id']}"
        room = rooms.setdefault(key, {
            "room_id": c["room_id"], "location_id": c["location_id"], "name": c["location"],
            "floor": c["floor_name"], "items": [], "pending": 0, "kept": 0, "changed": 0,
            "schedule_total": 0.0, "actual_total": 0.0,
        })
        room["items"].append(c)
        room["schedule_total"] = round(room["schedule_total"] + (c["qty"] or 0), 3)
        if c["verify_status"] == "pending":
            room["pending"] += 1
        elif c["verify_status"] == "kept":
            room["kept"] += 1
            room["actual_total"] = round(room["actual_total"] + (c["qty"] or 0), 3)
        elif c["verify_status"] == "changed":
            room["changed"] += 1
            room["actual_total"] = round(room["actual_total"] + (c["actual_qty"] or 0), 3)
    out_rooms = []
    for room in rooms.values():
        if room_id and room["room_id"] != room_id:
            continue
        room["verified"] = (room["pending"] == 0)
        room["progress_pct"] = round(100.0 * (room["kept"] + room["changed"]) / max(1, len(room["items"])), 1)
        if not (only_pending and room["pending"] == 0):
            out_rooms.append(room)
    out_rooms.sort(key=lambda r: (-r["pending"], r["name"]))
    totals = {
        "locations": len(out_rooms),
        "cells": sum(len(r["items"]) for r in out_rooms),
        "pending": sum(r["pending"] for r in out_rooms),
        "kept": sum(r["kept"] for r in out_rooms),
        "changed": sum(r["changed"] for r in out_rooms),
    }
    totals["progress_pct"] = round(100.0 * (totals["kept"] + totals["changed"]) / max(1, totals["cells"]), 1)
    return {"project_id": project_id, "docs": docs, "rooms": out_rooms, "totals": totals}


def verify_cell(cell_id: int, action: str, actual_qty: float | None, user: dict | None,
                note: str = "", push_measurement: bool = True) -> dict:
    """`keep`  -> confirmed as per the descriptive schedule.
       `change`-> actual quantity at site; also writes a measurement so Form-23 follows."""
    from fastapi import HTTPException
    cell = q1("""SELECT c.*, l.name AS location, l.room_id AS room_id, pi.unit AS unit, pi.project_id AS pid
                 FROM schedule_cells c JOIN schedule_locations l ON l.id=c.location_id
                 LEFT JOIN project_items pi ON pi.id=c.project_item_id WHERE c.id=?""", (cell_id,))
    if not cell:
        raise HTTPException(404, "Schedule cell not found")
    if action not in ("keep", "change", "not_applicable", "pending"):
        raise HTTPException(400, "action must be keep | change | not_applicable | pending")
    if action == "change" and actual_qty is None:
        raise HTTPException(400, "actual_qty is required when the quantity differs from the schedule")
    if not cell["project_item_id"] and action != "pending":
        raise HTTPException(400, "This schedule column is not linked to an estimate item yet. "
                                 "Link it (or create an extra item) before verifying.")
    if action == "pending":                          # undo a verification
        mid = cell["measurement_id"]
        if mid:
            m = q1("SELECT notes FROM measurements WHERE id=?", (mid,))
            if m and (m["notes"] or "").startswith("[Schedule"):     # only auto-written rows
                ex("DELETE FROM measurements WHERE id=?", (mid,))
            mid = None
        ex("""UPDATE schedule_cells SET verify_status='pending', actual_qty=NULL, measurement_id=NULL,
                  checked_by=?, checked_at=? WHERE id=?""", (user["id"] if user else None, now_iso(), cell_id))
        if user:
            audit(user, "SCHEDULE_VERIFY_RESET", "schedule_cells", cell_id, {"item": cell["col_label"]})
        return {"ok": True, "cell_id": cell_id, "status": "pending", "qty": None, "measurement_id": None}
    ts = now_iso()
    mid = cell["measurement_id"]
    qty = None
    if action == "keep":
        qty = float(cell["qty"] or 0)
        status = "kept"
    elif action == "change":
        qty = float(actual_qty)
        status = "kept" if abs(qty - float(cell["qty"] or 0)) < 1e-9 else "changed"
    else:
        status = "not_applicable"

    if action in ("keep", "change") and push_measurement:
        rid = cell["room_id"]
        if rid is None:
            rid, _s = ensure_room(cell["pid"], cell["location"])
        stamp = f"[Schedule {action}] {cell['col_label']}"
        if mid:
            ex("""UPDATE measurements SET room_id=?, nos=?, measured_qty=?, notes=?, measured_by=?,
                        measured_on=?, created_at=? WHERE id=?""",
               (rid, qty if qty else 1, qty or 0, (note or stamp)[:500], user["id"] if user else None,
                now_iso()[:10], ts, mid))
        else:
            mid = ex("""INSERT INTO measurements (project_id, project_item_id, room_id, length, breadth,
                            height, nos, measured_qty, notes, measured_by, measured_on, status, created_at)
                        VALUES (?,?,?,0,0,0,?,?,?,?,?, 'submitted', ?)""",
                     (cell["pid"], cell["project_item_id"], rid, qty if qty else 1, qty or 0,
                      (note or stamp)[:500], user["id"] if user else None, now_iso()[:10], ts)).lastrowid

    ex("""UPDATE schedule_cells SET verify_status=?, actual_qty=?, measurement_id=?, note=?, checked_by=?,
              checked_at=? WHERE id=?""",
       (status, qty, mid, note[:400] if note else cell["note"], user["id"] if user else None, ts, cell_id))
    if user:
        audit(user, "SCHEDULE_VERIFIED", "schedule_cells", cell_id,
              {"action": action, "schedule_qty": cell["qty"], "actual_qty": qty,
               "location": cell["location"], "item": cell["col_label"]})
    total = q1("SELECT COALESCE(SUM(measured_qty),0) AS q FROM measurements WHERE project_item_id=?",
               (cell["project_item_id"],))["q"]
    return {"ok": True, "cell_id": cell_id, "status": status, "qty": qty, "measurement_id": mid,
            "item_total_qty": round(total, 3)}


def verify_bulk(doc_id: int, user: dict | None, *, room_id: int | None = None, column_order: int | None = None,
                location_ids: list[int] | None = None, action: str = "keep",
                actuals: dict[str, float] | None = None) -> dict:
    """Verify a whole room (room_id) or a whole item (column_order) in one tap - mobile friendly."""
    sql = "SELECT c.id, c.qty FROM schedule_cells c JOIN schedule_locations l ON l.id=c.location_id WHERE c.doc_id=?"
    params: list[Any] = [doc_id]
    if room_id is not None:
        sql += " AND l.room_id=?"; params.append(room_id)
    if location_ids:
        sql += " AND c.location_id IN (" + ",".join("?" * len(location_ids)) + ")"; params.extend(location_ids)
    if column_order is not None:
        sql += " AND c.column_order=?"; params.append(column_order)
    if action == "keep":
        sql += " AND c.verify_status<>'kept'"
    rows = rows_to_dicts(q(sql, tuple(params)))
    done, failed = [], []
    actuals = actuals or {}
    for r in rows:
        actual = actuals.get(str(r["id"])) or actuals.get(r["id"])
        try:
            verify_cell(r["id"], action, actual if actual is not None else (None if action == "change" else r["qty"]),
                        user, push_measurement=(action != "not_applicable"))
            done.append(r["id"])
        except Exception as exc:                       # keep going - report the rest
            failed.append({"cell_id": r["id"], "error": str(exc)})
    return {"ok": True, "verified": len(done), "failed": failed, "cell_ids": done}


def map_column(doc_id: int, column_order: int, user: dict | None, *, project_item_id: int | None = None,
               master_item_id: int | None = None, create_item: bool = False, spec: dict | None = None) -> dict:
    """Point a schedule column at an estimate item / Master CSR item, or create an extra item."""
    from fastapi import HTTPException
    doc = q1("SELECT * FROM schedule_docs WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Schedule document not found")
    project_id = doc["project_id"]
    spec = spec or {}
    if create_item:
        project_item_id = _create_extra_item(project_id, spec, user)
    if not project_item_id and not master_item_id:
        raise HTTPException(400, "Provide project_item_id, master_item_id or create_item=true")
    row = None
    if project_item_id:
        row = q1("SELECT * FROM project_items WHERE id=? AND project_id=?", (project_item_id, project_id))
        if not row:
            raise HTTPException(404, "Project item not found in this project")
        master_item_id, item_code = row["master_item_id"], row["item_code"]
    else:
        row = q1("SELECT * FROM master_items WHERE id=?", (master_item_id,))
        if not row:
            raise HTTPException(404, "Master CSR item not found")
        order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM project_items WHERE project_id=?",
                    (project_id,))["m"] or 0) + 1
        project_item_id = ex("""INSERT INTO project_items (project_id, master_item_id, item_code, description,
                                    unit, rate, tendered_qty, is_non_schedule, source, confidence, match_method,
                                    sort_order, created_at)
                                VALUES (?,?,?,?,?,?,0,0,'extra',1.0,'schedule_column',?,?)""",
                             (project_id, row["id"], row["item_code"], row["description"], row["unit"],
                              row["rate"], order, now_iso())).lastrowid
        item_code = row["item_code"]
    ex("""UPDATE schedule_cells SET project_item_id=?, master_item_id=?, item_code=?, match_confidence=1.0,
              match_method='manual' WHERE doc_id=? AND column_order=?""",
       (project_item_id, master_item_id, item_code, doc_id, column_order))
    if user:
        audit(user, "SCHEDULE_COLUMN_MAPPED", "schedule_docs", doc_id,
              {"column_order": column_order, "item_code": item_code, "project_item_id": project_item_id})
    return {"ok": True, "column_order": column_order, "project_item_id": project_item_id, "item_code": item_code}


def _create_extra_item(project_id: int, spec: dict, user: dict | None) -> int:
    """A schedule column absent from the estimate becomes an extra (non-schedule) project item."""
    order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM project_items WHERE project_id=?",
                (project_id,))["m"] or 0) + 1
    return ex("""INSERT INTO project_items (project_id, master_item_id, item_code, description, unit, rate,
                    tendered_qty, is_non_schedule, ns_reason, source, confidence, match_method, sort_order,
                    created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (project_id, spec.get("master_item_id"), spec.get("item_code") or "SCHED",
               spec.get("description") or spec.get("col_label") or "Schedule item",
               spec.get("unit") or "Each", float(spec.get("rate") or 0), float(spec.get("tendered_qty") or 0),
               0 if spec.get("master_item_id") else 1,
               "" if spec.get("master_item_id") else "In descriptive schedule, not in estimate abstract",
               "extra", 1.0, "schedule_column", order, now_iso())).lastrowid


def reconciliation_rows(project_id: int, doc_id: int | None = None) -> list[list]:
    """Flat rows for the Excel export of the control sheet."""
    rec = reconciliation(project_id, doc_id)
    out = [["Item code", "Description", "Unit", "Rate", "Estimate qty", "Schedule qty",
            "Difference (schedule - estimate)", "Difference value", "Actual measured",
            "Rooms verified", "Rooms total"]]
    for r in rec["rows"]:
        if not r["locations"]:
            continue
        done = sum(1 for l in r["locations"] if l["verify_status"] != "pending")
        out.append([r["item_code"], r["description"], r["unit"] or "", r["rate"] or 0,
                    r["tendered_qty"], r["schedule_qty"], r["schedule_vs_tendered"],
                    round(r["schedule_vs_tendered"] * (r["rate"] or 0), 2), r["measured_qty"],
                    done, len(r["locations"])])
    out.append([])
    t = rec["totals"]
    out.append(["TOTAL", "", "", "", "", "", "", round(t["schedule_amount"] - t["tendered_amount"], 2),
                "", "", ""])
    return out
