"""
Smart-MB :: FastAPI application
Centralized CSR Database & Site Verification System - Maharashtra PWD (Electrical)

Routers
  /api/auth        login / session
  /api/csr         Master CSR database (browse, search, import, export)
  /api/projects    projects, estimates, smart import, checklist, deviations
  /api/measure     joint measurement recording + photo evidence
  /api/reports     Form-23 MB (PDF / Excel)
  /api/admin       master DB control, users, audit trail
Static single-page app served from /web
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException, Query,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, parsing, reports, seed
from .db import PHOTO_DIR, SAMPLE_DIR, UPLOAD_DIR, audit, ex, json_load, now_iso, q, q1, rows_to_dicts

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")

app = FastAPI(title="Smart-MB | Maharashtra PWD Electrical", version="1.0.0",
              description="Centralized CSR Database & Site Verification System")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    seed.ensure_seed()


# --------------------------------------------------------------- helpers / auth
class LoginIn(BaseModel):
    email: str
    password: str


class ProjectIn(BaseModel):
    name: str
    scheme: str = ""
    division: str = ""
    circle: str = ""
    region: str = "Pune"
    estimate_no: str = ""
    ts_no: str = ""
    ts_date: str = ""
    ts_amount: float = 0
    csr_fy: str = seed.FYS[-1]
    csr_region: str = "Pune"
    mb_no: str = ""
    agreement_no: str = ""
    agency: str = ""
    rooms: list[dict] = Field(default_factory=list)


class ItemIn(BaseModel):
    code: str = ""
    description: str = ""
    unit: str = "Each"
    rate: float = 0
    tendered_qty: float = 0
    is_non_schedule: bool = False
    ns_reason: str = ""
    source: str = "extra"
    master_item_id: Optional[int] = None


class MeasurementIn(BaseModel):
    project_item_id: int
    room_id: Optional[int] = None
    length: float = 0
    breadth: float = 0
    height: float = 0
    nos: float = 1
    measured_qty: Optional[float] = None
    notes: str = ""
    measured_on: str = ""
    measured_by: Optional[int] = None


class ConfirmImportIn(BaseModel):
    rows: list[dict]
    create_rooms_from_text: bool = True


def user_public(row: Any) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    return d


def current_user(authorization: str | None = Header(default=None), token: str | None = None) -> dict:
    """Session resolution: Bearer header for XHR calls, or ?token= for direct
    download links / <img> tags opened outside the SPA's fetch layer."""
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    raw = bearer or (token or "")
    if not raw:
        raise HTTPException(401, "Missing bearer token")
    payload = auth.decode_token(raw)
    if not payload:
        raise HTTPException(401, "Invalid or expired session - please sign in again")
    row = q1("SELECT * FROM users WHERE id = ? AND is_active = 1", (payload["uid"],))
    if not row:
        raise HTTPException(401, "User not found or deactivated")
    return dict(row)


def admin_only(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Super Admin privileges required for this action")
    return user


def compute_qty(unit: str, nos: float, length: float, breadth: float, height: float) -> float:
    u = (unit or "").strip().lower()
    if u in ("m", "rm", "meter", "metre"):
        return round((nos or 1) * (length or 0), 3)
    if u in ("sqm", "sq.m"):
        return round((nos or 1) * (length or 0) * (breadth or 0), 3)
    if u in ("cum", "cu.m"):
        return round((nos or 1) * (length or 0) * (breadth or 0) * (height or 0), 3)
    if u in ("kg",):
        return round((nos or 1) * (length or 1), 3)
    if (length or 0) > 0 or (breadth or 0) > 0 or (height or 0) > 0:
        return round((nos or 1) * (length or 0) * (breadth or 1) * (height or 1), 3)
    return round(nos or 0, 3)


def master_index(fy: str, region: str) -> dict[str, dict]:
    rows = rows_to_dicts(q(
        "SELECT id, item_code, description, short_desc, unit, rate, category, section, spec_no, chapter, tags"
        " FROM master_items WHERE fy = ? AND region = ? AND is_active = 1", (fy, region)))
    return parsing.build_master_index(rows)


# ===================================================================== AUTH
@app.post("/api/auth/login")
def login(body: LoginIn):
    row = q1("SELECT * FROM users WHERE lower(email) = lower(?)", (body.email.strip(),))
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    if not row["is_active"]:
        raise HTTPException(403, "Account deactivated - contact the Super Admin")
    ex("UPDATE users SET last_login = ? WHERE id = ?", (now_iso(), row["id"]))
    audit(dict(row), "LOGIN", "users", row["id"])
    return {"token": auth.create_token(dict(row)), "user": user_public(row)}


@app.post("/api/auth/register")
def register(name: str = Form(...), email: str = Form(...), password: str = Form(...),
             designation: str = Form("Junior Engineer (Electrical)"), division: str = Form(""),
             circle: str = Form(""), region: str = Form("Pune"), phone: str = Form("")):
    if q1("SELECT id FROM users WHERE lower(email) = lower(?)", (email,)):
        raise HTTPException(409, "An account with this email already exists")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    uid = ex("INSERT INTO users (name,email,password_hash,role,designation,division,circle,region,phone,"
             "is_active,created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
             (name, email, auth.hash_password(password), "engineer", designation, division, circle,
              region, phone, now_iso())).lastrowid
    row = q1("SELECT * FROM users WHERE id = ?", (uid,))
    audit(dict(row), "USER_REGISTERED", "users", uid, designation)
    return {"token": auth.create_token(dict(row)), "user": user_public(row)}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": user_public(user)}


# =============================================================== MASTER CSR
@app.get("/api/csr/versions")
def csr_versions(user: dict = Depends(current_user)):
    return {"versions": rows_to_dicts(q(
        "SELECT v.*, (SELECT COUNT(*) FROM master_items m WHERE m.fy=v.fy AND m.region=v.region) AS live_count"
        " FROM csr_versions v ORDER BY v.fy DESC, v.region"))}


@app.get("/api/csr/facets")
def csr_facets(fy: str = Query(...), region: str = Query(...), user: dict = Depends(current_user)):
    cats = rows_to_dicts(q(
        "SELECT category AS value, COUNT(*) AS count, AVG(rate) AS avg_rate FROM master_items"
        " WHERE fy=? AND region=? AND is_active=1 GROUP BY category ORDER BY category", (fy, region)))
    chapters = rows_to_dicts(q(
        "SELECT chapter AS value, COUNT(*) AS count FROM master_items WHERE fy=? AND region=? AND is_active=1"
        " GROUP BY chapter ORDER BY chapter", (fy, region)))
    for c in chapters:
        c["name"] = seed.CHAPTER_NAMES.get(c["value"], f"Chapter {c['value']}")
    units = rows_to_dicts(q(
        "SELECT unit AS value, COUNT(*) AS count FROM master_items WHERE fy=? AND region=? AND is_active=1"
        " GROUP BY unit ORDER BY count DESC", (fy, region)))
    tags = {}
    for r in q("SELECT tags FROM master_items WHERE fy=? AND region=? AND is_active=1", (fy, region)):
        for t in json_load(r["tags"], []):
            tags[t] = tags.get(t, 0) + 1
    return {"categories": cats, "chapters": chapters, "units": units,
            "tags": [{"value": k, "count": v} for k, v in sorted(tags.items(), key=lambda x: -x[1])]}


@app.get("/api/csr/items")
def csr_items(fy: str = Query(...), region: str = Query(...), qtext: str = Query("", alias="q"),
              category: str = "", chapter: Optional[int] = None, tag: str = "",
              limit: int = 60, offset: int = 0, user: dict = Depends(current_user)):
    where = ["fy = ?", "region = ?", "is_active = 1"]
    params: list[Any] = [fy, region]
    if qtext:
        where.append("(item_code LIKE ? OR lower(description) LIKE ? OR lower(short_desc) LIKE ? OR lower(tags) LIKE ?)")
        like = f"%{qtext.lower()}%"
        params += [f"%{qtext}%", like, like, like]
    if category:
        where.append("category = ?")
        params.append(category)
    if chapter is not None:
        where.append("chapter = ?")
        params.append(chapter)
    if tag:
        where.append("lower(tags) LIKE ?")
        params.append(f"%{tag.lower()}%")
    sql_where = " AND ".join(where)
    total = q1(f"SELECT COUNT(*) AS c FROM master_items WHERE {sql_where}", params)["c"]
    rows = rows_to_dicts(q(
        f"SELECT * FROM master_items WHERE {sql_where} ORDER BY chapter, item_code LIMIT ? OFFSET ?",
        params + [min(limit, 300), offset]))
    for r in rows:
        r["tags"] = json_load(r.pop("tags", "[]"), [])
    return {"total": total, "items": rows, "limit": limit, "offset": offset}


@app.get("/api/csr/suggest")
def csr_suggest(qtext: str = Query(..., alias="q"), fy: str = Query(""), region: str = Query(""),
                limit: int = 12, user: dict = Depends(current_user)):
    """Natural-language search used by the 'Add Extra Item' flow: typing 'Exhaust Fan'
    returns ranked Master CSR items with the correct code and rate attached."""
    fy = fy or seed.FYS[-1]
    region = region or (user.get("region") or "Pune")
    tokens = [t for t in re.split(r"\W+", qtext.lower()) if len(t) > 2]
    rows = rows_to_dicts(q(
        "SELECT * FROM master_items WHERE fy=? AND region=? AND is_active=1", (fy, region)))
    scored = []
    for r in rows:
        tags = json_load(r["tags"], [])
        blob = f"{r['item_code']} {r['short_desc'] or ''} {r['description']} {r['section'] or ''} {' '.join(tags)} {r['spec_no'] or ''}".lower()
        score = 0.0
        for t in tokens:
            if t == r["item_code"].lower():
                score += 12
            if t in [x.lower() for x in tags]:
                score += 6
            score += blob.count(t) * (1.6 if len(t) > 4 else 1.0)
            if re.search(rf"\b{re.escape(t)}", blob):
                score += 1.2
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["item_code"]))
    out = []
    for s, r in scored[:limit]:
        r["tags"] = json_load(r.pop("tags", "[]"), [])
        r["score"] = round(s, 2)
        out.append(r)
    return {"query": qtext, "fy": fy, "region": region, "results": out}


@app.get("/api/csr/export")
def csr_export(fy: str = Query(...), region: str = Query(...), user: dict = Depends(current_user)):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = f"CSR {fy} {region}"
    ws.append(["Item No", "Description", "Unit", "Material Rate", "Labour Rate", "Completed Rate",
               "Chapter", "Section", "Category", "Spec No", "Tags", "Is New"])
    for r in q("SELECT * FROM master_items WHERE fy=? AND region=? ORDER BY chapter, item_code", (fy, region)):
        ws.append([r["item_code"], r["description"], r["unit"], r["material_rate"], r["labour_rate"], r["rate"],
                   r["chapter"], r["section"], r["category"], r["spec_no"],
                   ", ".join(json_load(r["tags"], [])), "Yes" if r["is_new"] else ""])
    for i, w in enumerate([10, 90, 8, 13, 13, 14, 9, 34, 18, 14, 26, 8], start=1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    audit(user, "CSR_EXPORTED", "master_items", f"{fy}/{region}")
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="MasterCSR_{fy}_{region}.xlsx"'})


@app.post("/api/csr/import")
async def csr_import(file: UploadFile = File(...), fy: str = Form(...), region: str = Form(...),
                     mode: str = Form("merge"), commit: bool = Form(False),
                     user: dict = Depends(admin_only)):
    """Admin: upload/refresh the Master CSR (Excel or CSV).  Two-step: preview then commit."""
    raw = await file.read()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "csr.xlsx")
    path = os.path.join(UPLOAD_DIR, f"csr_{uuid.uuid4().hex[:8]}_{safe}")
    with open(path, "wb") as fh:
        fh.write(raw)

    rows: list[list[Any]] = []
    ext = os.path.splitext(safe)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None and str(c).strip() for c in row):
                        rows.append(list(row))
        elif ext in (".csv", ".txt"):
            import csv as _csv
            text = raw.decode("utf-8-sig", errors="replace")
            rows = [r for r in _csv.reader(io.StringIO(text)) if any(str(c).strip() for c in r)]
        else:
            raise HTTPException(400, "Upload an .xlsx, .xlsm or .csv file")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file: {exc}")

    parsed = parsing.parse_csr_rows(rows)
    if not parsed["ok"]:
        return JSONResponse({"ok": False, "error": parsed["error"], "preview": [
            [str(c) for c in r] for r in parsed.get("preview", [])]}, status_code=200)

    existing = {r["item_code"] for r in q("SELECT item_code FROM master_items WHERE fy=? AND region=?", (fy, region))}
    new_codes = [i["item_code"] for i in parsed["items"] if i["item_code"] not in existing]
    updates = [i["item_code"] for i in parsed["items"] if i["item_code"] in existing]

    response = {
        "ok": True, "columns_detected": parsed["columns"], "header_row": parsed["header_row"],
        "items": parsed["items"][:200], "item_count": len(parsed["items"]),
        "new": len(new_codes), "updates": len(updates), "errors": parsed["errors"],
        "error_count": parsed["error_count"], "committed": False, "fy": fy, "region": region,
    }
    if not commit:
        return response

    ts = now_iso()
    if mode == "replace":
        ex("DELETE FROM master_items WHERE fy=? AND region=?", (fy, region))
    for it in parsed["items"]:
        tags = json.dumps(it["tags"] or [])
        exist = q1("SELECT id FROM master_items WHERE fy=? AND region=? AND item_code=?", (fy, region, it["item_code"]))
        if exist:
            ex("UPDATE master_items SET description=?, unit=?, rate=?, material_rate=?, labour_rate=?,"
               " category=?, section=?, spec_no=?, tags=?, chapter=?, short_desc=?, is_active=1, updated_at=?"
               " WHERE id=?", (it["description"], it["unit"], it["rate"], it["material_rate"], it["labour_rate"],
                               it["category"], it["section"], it["spec_no"], tags, it["chapter"],
                               seed._short(it["description"]),
                               ts, exist["id"]))
        else:
            ex("INSERT INTO master_items (fy, region, item_code, description, short_desc, unit, rate, material_rate,"
               " labour_rate, chapter, section, category, spec_no, tags, is_new, is_active, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?,?)",
               (fy, region, it["item_code"], it["description"], it["description"][:74], it["unit"], it["rate"],
                it["material_rate"], it["labour_rate"], it["chapter"], it["section"], it["category"],
                it["spec_no"], tags, ts, ts))
    ver = q1("SELECT id FROM csr_versions WHERE fy=? AND region=?", (fy, region))
    live = q1("SELECT COUNT(*) AS c FROM master_items WHERE fy=? AND region=?", (fy, region))["c"]
    if ver:
        ex("UPDATE csr_versions SET item_count=?, source_file=?, uploaded_by=?, uploaded_at=?, status='active'"
           " WHERE id=?", (live, safe, user["id"], ts, ver["id"]))
    else:
        ex("INSERT INTO csr_versions (fy, region, status, item_count, source_file, uploaded_by, uploaded_at)"
           " VALUES (?,?,?,?,?,?,?)", (fy, region, "active", live, safe, user["id"], ts))
    audit(user, "CSR_IMPORTED", "master_items", f"{fy}/{region}",
          {"mode": mode, "items": len(parsed["items"]), "new": len(new_codes), "updates": len(updates),
           "file": safe, "live": live})
    rows2 = rows_to_dicts(q("SELECT * FROM master_items WHERE fy=? AND region=? LIMIT 5", (fy, region)))
    return {**response, "committed": True, "live_count": live, "sample": rows2}


@app.delete("/api/csr/versions")
def delete_csr_version(fy: str = Query(...), region: str = Query(...), user: dict = Depends(admin_only)):
    """Remove a whole FY × region rate set (e.g. a wrongly imported year)."""
    if not q1("SELECT id FROM csr_versions WHERE fy=? AND region=?", (fy, region)):
        raise HTTPException(404, f"CSR version {fy} / {region} not found")
    ex("DELETE FROM master_items WHERE fy=? AND region=?", (fy, region))
    ex("DELETE FROM csr_versions WHERE fy=? AND region=?", (fy, region))
    audit(user, "CSR_VERSION_DELETED", "csr_versions", f"{fy}/{region}")
    return {"ok": True, "deleted": {"fy": fy, "region": region}}


@app.post("/api/csr/items/{item_id}/tags")
def update_tags(item_id: int, tags: list[str], user: dict = Depends(admin_only)):
    if not q1("SELECT id FROM master_items WHERE id=?", (item_id,)):
        raise HTTPException(404, "Item not found")
    ex("UPDATE master_items SET tags=?, updated_at=? WHERE id=?", (json.dumps(tags), now_iso(), item_id))
    audit(user, "CSR_TAG_UPDATED", "master_items", item_id, tags)
    return {"ok": True, "tags": tags}


# ================================================================== PROJECTS
def project_stats(pid: int) -> dict:
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    tendered = q1("SELECT COALESCE(SUM(tendered_qty*rate),0) AS amt, COUNT(*) AS items FROM project_items"
                  " WHERE project_id=?", (pid,))
    measured = q1("SELECT COALESCE(SUM(m.measured_qty),0) AS qty, COUNT(*) AS rows_count"
                  " FROM measurements m WHERE m.project_id=?", (pid,))
    # measured value per item (aggregate then join rate)
    mv = q1("""SELECT COALESCE(SUM(x.qty * x.rate),0) AS amt, COUNT(*) AS done_items FROM (
                 SELECT pi.id, pi.rate, COALESCE(SUM(mm.measured_qty),0) AS qty
                 FROM project_items pi LEFT JOIN measurements mm ON mm.project_item_id = pi.id
                 WHERE pi.project_id = ? GROUP BY pi.id) x WHERE x.qty > 0""", (pid,)) or {"amt": 0, "done_items": 0}
    total_items = tendered["items"] or 0
    dev = deviation_rows(pid)
    over = [d for d in dev if d["dev_qty"] > 0.001]
    under = [d for d in dev if d["dev_qty"] < -0.001]
    critical = [d for d in dev if d["tendered"] and abs(d["dev_pct"]) >= 10]
    ns = q1("SELECT COUNT(*) AS c FROM project_items WHERE project_id=? AND is_non_schedule=1", (pid,))["c"]
    rooms = q1("SELECT COUNT(*) AS c FROM rooms WHERE project_id=?", (pid,))["c"]
    photos = q1("SELECT COUNT(*) AS c FROM measurement_photos ph JOIN measurements m ON m.id=ph.measurement_id"
                " WHERE m.project_id=?", (pid,))["c"]
    return {
        "project": dict(p),
        "tendered_amount": round(tendered["amt"], 2),
        "measured_amount": round(mv["amt"], 2),
        "net_deviation": round(mv["amt"] - tendered["amt"], 2),
        "items": total_items,
        "items_measured": mv["done_items"],
        "items_pending": max(0, total_items - mv["done_items"]),
        "progress_pct": round((mv["done_items"] / total_items * 100) if total_items else 0, 1),
        "measurement_rows": measured["rows_count"],
        "measured_qty_sum": round(measured["qty"], 3),
        "excess_items": len(over),
        "saving_items": len(under),
        "critical_deviations": len(critical),
        "excess_amount": round(sum(d["dev_amount"] for d in over), 2),
        "saving_amount": round(sum(d["dev_amount"] for d in under), 2),
        "non_schedule_items": ns,
        "rooms": rooms,
        "photos": photos,
    }


def deviation_rows(pid: int) -> list[dict]:
    rows = q("""SELECT pi.id, pi.item_code, pi.description, pi.unit, pi.rate, pi.tendered_qty,
                       pi.is_non_schedule,
                       COALESCE(SUM(m.measured_qty),0) AS measured_qty
                FROM project_items pi LEFT JOIN measurements m ON m.project_item_id = pi.id
                WHERE pi.project_id = ? GROUP BY pi.id ORDER BY pi.sort_order, pi.id""", (pid,))
    out = []
    for r in rows:
        tq, mq, rate = r["tendered_qty"] or 0, r["measured_qty"] or 0, r["rate"] or 0
        dev = round(mq - tq, 3)
        pct = (dev / tq * 100) if tq else (100.0 if mq else 0.0)
        out.append({
            "project_item_id": r["id"], "item_code": r["item_code"],
            "description": r["description"], "unit": r["unit"], "rate": rate,
            "tendered": tq, "measured": mq, "dev_qty": dev, "dev_pct": round(pct, 2),
            "dev_amount": round(dev * rate, 2), "is_non_schedule": r["is_non_schedule"],
            "status": "pending" if mq == 0 else ("excess" if dev > 0.001 else ("saving" if dev < -0.001 else "matched")),
            "severity": ("critical" if tq and abs(pct) >= 20 else
                         "review" if tq and abs(pct) >= 10 else
                         "info" if abs(dev) > 0.001 else "ok"),
        })
    return out


@app.get("/api/projects")
def list_projects(user: dict = Depends(current_user)):
    if user["role"] == "admin":
        rows = q("SELECT p.*, u.name AS engineer_name FROM projects p LEFT JOIN users u ON u.id=p.engineer_id"
                 " ORDER BY p.created_at DESC")
    else:
        rows = q("SELECT p.*, u.name AS engineer_name FROM projects p LEFT JOIN users u ON u.id=p.engineer_id"
                 " WHERE p.engineer_id=? ORDER BY p.created_at DESC", (user["id"],))
    out = []
    for r in rows:
        d = dict(r)
        agg = q1("""SELECT COUNT(*) AS items,
                           COALESCE(SUM(tendered_qty*rate),0) AS tendered
                    FROM project_items WHERE project_id=?""", (r["id"],))
        d["items"] = agg["items"]
        d["tendered_amount"] = round(agg["tendered"], 2)
        d["measured_amount"] = round(q1("""SELECT COALESCE(SUM(x.qty*x.rate),0) AS amt FROM (
                SELECT pi.id, pi.rate, COALESCE(SUM(mm.measured_qty),0) AS qty FROM project_items pi
                LEFT JOIN measurements mm ON mm.project_item_id=pi.id WHERE pi.project_id=? GROUP BY pi.id) x""",
                                        (r["id"],))["amt"], 2)
        d["progress_pct"] = round((d["measured_amount"] / d["tendered_amount"] * 100)
                                  if d["tendered_amount"] else 0, 1)
        out.append(d)
    return {"projects": out}


@app.post("/api/projects")
def create_project(body: ProjectIn, user: dict = Depends(current_user)):
    ts = now_iso()
    seq = (q1("SELECT COUNT(*) AS c FROM projects")["c"] or 0) + 1
    code = f"PWDE/ELE/{(body.region or 'Pune').upper()[:6]}/{body.csr_fy}/{seq:03d}"
    pid = ex("""INSERT INTO projects (project_code, name, scheme, division, circle, region, engineer_id,
                estimate_no, ts_no, ts_date, ts_amount, csr_fy, csr_region, mb_no, agreement_no, agency,
                status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?, ?)""",
             (code, body.name, body.scheme, body.division or user.get("division", ""), body.circle or user.get("circle", ""),
              body.region, user["id"], body.estimate_no, body.ts_no, body.ts_date, body.ts_amount,
              body.csr_fy, body.csr_region, body.mb_no or "MB-01", body.agreement_no, body.agency, ts, ts)).lastrowid
    if body.rooms:
        for i, r in enumerate(body.rooms, start=1):
            ex("INSERT INTO rooms (project_id, floor, name, sort_order, created_at) VALUES (?,?,?,?,?)",
               (pid, r.get("floor", ""), r.get("name", f"Room {i}"), i, ts))
    audit(user, "PROJECT_CREATED", "projects", pid, body.name)
    return {"ok": True, "project_id": pid, "project_code": code}


@app.get("/api/projects/{pid}")
def get_project(pid: int, user: dict = Depends(current_user)):
    stats = project_stats(pid)
    stats["rooms"] = rows_to_dicts(q("SELECT * FROM rooms WHERE project_id=? ORDER BY sort_order, id", (pid,)))
    stats["team"] = user_public(q1("SELECT * FROM users WHERE id=?", (stats["project"]["engineer_id"],))) \
        if stats["project"]["engineer_id"] else None
    stats["parse_jobs"] = rows_to_dicts(q(
        "SELECT id, filename, file_type, engine, anchors, matched, unknown, created_at FROM parse_jobs"
        " WHERE project_id=? ORDER BY id DESC LIMIT 8", (pid,)))
    return stats


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: dict, user: dict = Depends(current_user)):
    allowed = {"name", "scheme", "division", "circle", "region", "estimate_no", "ts_no", "ts_date", "ts_amount",
               "csr_fy", "csr_region", "mb_no", "agreement_no", "agency", "status"}
    sets, params = [], []
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise HTTPException(400, "No valid fields supplied")
    params += [now_iso(), pid]
    ex(f"UPDATE projects SET {', '.join(sets)}, updated_at=? WHERE id=?", params)
    audit(user, "PROJECT_UPDATED", "projects", pid, body)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, user: dict = Depends(current_user)):
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    if user["role"] != "admin" and p["engineer_id"] != user["id"]:
        raise HTTPException(403, "Only the owning engineer or a Super Admin can delete this project")
    ex("DELETE FROM projects WHERE id=?", (pid,))          # cascades to items/rooms/measurements
    audit(user, "PROJECT_DELETED", "projects", pid, p["name"])
    return {"ok": True}


@app.post("/api/projects/{pid}/rooms")
def add_room(pid: int, body: dict, user: dict = Depends(current_user)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Room name is required")
    order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM rooms WHERE project_id=?", (pid,))["m"] or 0) + 1
    rid = ex("INSERT INTO rooms (project_id, floor, name, sort_order, created_at) VALUES (?,?,?,?,?)",
             (pid, body.get("floor", ""), name, order, now_iso())).lastrowid
    audit(user, "ROOM_ADDED", "rooms", rid, name)
    return {"ok": True, "room_id": rid}


@app.delete("/api/rooms/{rid}")
def delete_room(rid: int, user: dict = Depends(current_user)):
    ex("DELETE FROM rooms WHERE id=?", (rid,))
    audit(user, "ROOM_DELETED", "rooms", rid)
    return {"ok": True}


@app.get("/api/projects/{pid}/items")
def project_items(pid: int, user: dict = Depends(current_user)):
    rows = rows_to_dicts(q("""SELECT pi.*, mi.tags AS master_tags, mi.spec_no, mi.category, mi.chapter,
                                     COALESCE(SUM(m.measured_qty),0) AS measured_qty,
                                     COUNT(m.id) AS measurement_rows
                              FROM project_items pi
                              LEFT JOIN master_items mi ON mi.id = pi.master_item_id
                              LEFT JOIN measurements m ON m.project_item_id = pi.id
                              WHERE pi.project_id = ?
                              GROUP BY pi.id ORDER BY pi.sort_order, pi.id""", (pid,)))
    for r in rows:
        r["master_tags"] = json_load(r.get("master_tags"), [])
        r["measured_qty"] = round(r["measured_qty"] or 0, 3)
    return {"items": rows}


@app.post("/api/projects/{pid}/items")
def add_project_item(pid: int, body: ItemIn, user: dict = Depends(current_user)):
    """Extra-item logic: the engineer picks an item from the Master CSR and the app
    attaches the correct item code, description, unit and rate automatically."""
    if not q1("SELECT id FROM projects WHERE id=?", (pid,)):
        raise HTTPException(404, "Project not found")
    p = dict(q1("SELECT * FROM projects WHERE id=?", (pid,)))
    mi = None
    if body.master_item_id:
        mi = q1("SELECT * FROM master_items WHERE id=?", (body.master_item_id,))
    if mi is None and body.code:
        mi = q1("SELECT * FROM master_items WHERE item_code=? AND fy=? AND region=?",
                (body.code, p["csr_fy"], p["csr_region"]))
    ts = now_iso()
    order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM project_items WHERE project_id=?", (pid,))["m"] or 0) + 1
    if mi:
        mi = dict(mi)
        if q1("SELECT id FROM project_items WHERE project_id=? AND item_code=? AND source IN ('extra','estimate')",
              (pid, mi["item_code"])):
            raise HTTPException(409, f"Item {mi['item_code']} is already present in this project")
        iid = ex("""INSERT INTO project_items (project_id, master_item_id, item_code, description, unit, rate,
                    tendered_qty, is_non_schedule, ns_reason, source, pdf_qty, confidence, match_method,
                    sort_order, created_at) VALUES (?,?,?,?,?,?,?,0,'',?,0,1.0,'master-lookup',?,?)""",
                 (pid, mi["id"], mi["item_code"], mi["description"], mi["unit"], mi["rate"],
                  body.tendered_qty, body.source or "extra", order, ts)).lastrowid
        audit(user, "EXTRA_ITEM_ADDED", "project_items", iid,
              {"code": mi["item_code"], "project": pid, "qty": body.tendered_qty})
        return {"ok": True, "project_item_id": iid, "master_item_id": mi["id"],
                "item_code": mi["item_code"], "description": mi["description"], "unit": mi["unit"],
                "rate": mi["rate"], "non_schedule": False}
    # non-schedule / manual item
    desc = (body.description or "").strip()
    if not desc:
        raise HTTPException(400, "Non-schedule items need a description (the code is not in the Master CSR)")
    code = (body.code or f"NS-{order}").strip()
    iid = ex("""INSERT INTO project_items (project_id, master_item_id, item_code, description, unit, rate,
                tendered_qty, is_non_schedule, ns_reason, source, pdf_qty, confidence, match_method,
                sort_order, created_at) VALUES (?,NULL,?,?,?,?,?,1,?,'manual',?,1.0,'manual-entry',?,?)""",
             (pid, code, desc, body.unit or "Each", body.rate or 0, body.tendered_qty, body.ns_reason,
              0, order, ts)).lastrowid
    audit(user, "NON_SCHEDULE_ITEM_ADDED", "project_items", iid, {"code": code, "desc": desc})
    return {"ok": True, "project_item_id": iid, "item_code": code, "description": desc,
            "unit": body.unit or "Each", "rate": body.rate or 0, "non_schedule": True}


@app.patch("/api/project-items/{iid}")
def patch_project_item(iid: int, body: dict, user: dict = Depends(current_user)):
    row = q1("SELECT * FROM project_items WHERE id=?", (iid,))
    if not row:
        raise HTTPException(404, "Project item not found")
    allowed = {"tendered_qty", "description", "unit", "rate", "ns_reason", "sort_order"}
    sets, params = [], []
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise HTTPException(400, "Nothing to update")
    params.append(iid)
    ex(f"UPDATE project_items SET {', '.join(sets)} WHERE id=?", params)
    audit(user, "PROJECT_ITEM_UPDATED", "project_items", iid, body)
    return {"ok": True}


@app.delete("/api/project-items/{iid}")
def delete_project_item(iid: int, user: dict = Depends(current_user)):
    ex("DELETE FROM measurements WHERE project_item_id=?", (iid,))
    ex("DELETE FROM project_items WHERE id=?", (iid,))
    audit(user, "PROJECT_ITEM_DELETED", "project_items", iid)
    return {"ok": True}


# --------------------------------------------------- SMART IMPORT (Flow B)
def _run_parse(text: str, fy: str, region: str, filename: str, engine: str) -> dict:
    idx = master_index(fy, region)
    result = parsing.parse_estimate_text(text, idx)
    result["master_index_size"] = len(idx)
    return result


def _parse_payload(pid: int, result: dict) -> dict:
    """Shape the parse result into editable confirmation rows."""
    rows = []
    for r in result["items"]:
        m = r["master"]
        rows.append({
            "include": True, "status": "matched", "printed_code": r["printed_code"],
            "item_code": r["item_code"], "pdf_qty": r["pdf_qty"] or 0, "unit": m["unit"],
            "description": m["description"], "short_desc": m.get("short_desc"),
            "rate": m["rate"], "master_item_id": m["id"], "confidence": r["confidence"],
            "method": r["method"], "flags": r["flags"], "raw_line": r["raw_line"], "line_no": r["line_no"],
            "is_non_schedule": False, "tendered_qty": r["pdf_qty"] or 0,
        })
    for r in result["unknown"]:
        rows.append({
            "include": True, "status": "unknown_item", "printed_code": r["printed_code"],
            "item_code": r["printed_code"], "pdf_qty": r["pdf_qty"] or 0, "unit": r.get("unit_hint") or "Each",
            "description": (r["raw_line"][:180] or "NON-SCHEDULE ITEM - description required"),
            "short_desc": None, "rate": 0, "master_item_id": None, "confidence": r["confidence"],
            "method": r["method"], "flags": r["flags"] or ["Not found in Master CSR - manual rate/description required"],
            "raw_line": r["raw_line"], "line_no": r["line_no"], "is_non_schedule": True,
            "tendered_qty": r["pdf_qty"] or 0,
        })
    return {"rows": rows, "stats": result["stats"], "text_excerpt": None, "ai_prompt": result["ai_prompt"],
            "engine": result["engine"], "duplicates": result.get("duplicates", [])}


@app.post("/api/projects/{pid}/parse-estimate")
async def parse_estimate(pid: int, file: UploadFile = File(...), engine: str = Form("anchor"),
                         user: dict = Depends(current_user)):
    """Upload the Technical Sanction Estimate (PDF/Excel/CSV)."""
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    raw = await file.read()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "estimate.pdf")
    path = os.path.join(UPLOAD_DIR, f"est_{pid}_{uuid.uuid4().hex[:8]}_{safe}")
    with open(path, "wb") as fh:
        fh.write(raw)
    try:
        doc = parsing.extract_any(path)
    except Exception as exc:
        raise HTTPException(400, f"Could not read the estimate: {exc}")
    text = doc["text"] or ""
    if doc.get("needs_ocr"):
        text = _apply_ocr_shim(text)
    result = _run_parse(text, p["csr_fy"], p["csr_region"], safe, engine)
    payload = _parse_payload(pid, result)
    payload.update({
        "filename": safe, "file_type": doc["file_type"], "needs_ocr": doc.get("needs_ocr", False),
        "text_excerpt": text[:6000], "ranks": 0, "project_id": pid,
        "engine_note": ("Text layer is sparse - the file is likely a scan. OCR shim + OCR-repair on item codes "
                        "applied; verify quantities flagged low-confidence."
                        if doc.get("needs_ocr") else "Digital text layer detected - layout column mapping used."),
    })
    audit(user, "ESTIMATE_PARSED", "projects", pid,
          {"file": safe, "matched": payload["stats"]["matched"], "unknown": payload["stats"]["unknown"],
           "engine": result["engine"]})
    return payload


def _apply_ocr_shim(text: str) -> str:
    """Very light OCR shim for scans: repair digit/letter confusion next to separators."""
    def fix(m: re.Match) -> str:
        return m.group(0).translate(parsing.OCR_MAP)
    return re.sub(r"\b[0-9OoIlSs]{1,2}-[0-9OoIlSs]{1,2}-[0-9OoIlSs]{1,2}\b", fix, text)


@app.post("/api/projects/{pid}/parse-text")
def parse_text(pid: int, body: dict, user: dict = Depends(current_user)):
    """Paste estimate / abstract text (mobile-friendly alternative to file upload)."""
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    text = body.get("text") or ""
    if len(text.strip()) < 8:
        raise HTTPException(400, "Paste the estimate table text (at least a couple of item rows)")
    result = _run_parse(text, p["csr_fy"], p["csr_region"], "pasted-text", body.get("engine", "anchor"))
    payload = _parse_payload(pid, result)
    payload.update({"filename": "pasted-text", "file_type": "text", "needs_ocr": False,
                    "text_excerpt": text[:6000], "project_id": pid,
                    "engine_note": "Direct text input - anchor-based extraction only."})
    audit(user, "ESTIMATE_PARSED", "projects", pid, {"source": "paste", "matched": payload["stats"]["matched"]})
    return payload


@app.post("/api/projects/{pid}/import-estimate")
def import_estimate(pid: int, body: ConfirmImportIn, user: dict = Depends(current_user)):
    """Step 2 of the Smart Import: engineer reviews the reconciliation table, edits/removes
    rows, then commits.  Measurement checklist is generated from the committed rows."""
    p = q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    ts = now_iso()
    order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM project_items WHERE project_id=?", (pid,))["m"] or 0)
    created, skipped = [], []
    for r in body.rows:
        if not r.get("include", True):
            skipped.append({"item_code": r.get("item_code"), "reason": "deselected"})
            continue
        code = str(r.get("item_code") or "").strip()
        if not code:
            skipped.append({"item_code": None, "reason": "missing item code"})
            continue
        if q1("SELECT id FROM project_items WHERE project_id=? AND item_code=?", (pid, code)):
            skipped.append({"item_code": code, "reason": "already present in project"})
            continue
        order += 1
        ns = 1 if r.get("is_non_schedule") else 0
        iid = ex("""INSERT INTO project_items (project_id, master_item_id, item_code, description, unit, rate,
                    tendered_qty, is_non_schedule, ns_reason, source, pdf_qty, confidence, match_method,
                    sort_order, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (pid, r.get("master_item_id"), code, r.get("description") or "NON-SCHEDULE ITEM",
                  r.get("unit") or "Each", r.get("rate") or 0, r.get("tendered_qty") or 0, ns,
                  r.get("ns_reason") or (r.get("flags") or [""])[0], "estimate",
                  r.get("pdf_qty"), r.get("confidence") or 0.0, r.get("method") or "manual", order, ts)).lastrowid
        created.append({"id": iid, "item_code": code, "non_schedule": bool(ns)})
        # auto-tag driven room hints
        if body.create_rooms_from_text:
            _auto_rooms(pid, r.get("raw_line") or "")
    total = sum((r.get("tendered_qty") or 0) * (r.get("rate") or 0) for r in body.rows if r.get("include", True))
    ex("UPDATE projects SET ts_amount = CASE WHEN ts_amount > 0 THEN ts_amount ELSE ? END, parse_summary=?,"
       " updated_at=? WHERE id=?", (round(total, 2), json.dumps({
           "imported": len(created), "skipped": len(skipped),
           "matched": sum(1 for r in body.rows if r.get("status") == "matched"),
           "unknown": sum(1 for r in body.rows if r.get("status") == "unknown_item")}), ts, pid))
    ex("INSERT INTO parse_jobs (project_id, filename, file_type, engine, anchors, matched, unknown, created_by,"
       " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
       (pid, "import-commit", "mixed", "anchor-regex-v1", len(body.rows),
        sum(1 for r in body.rows if r.get("status") == "matched"),
        sum(1 for r in body.rows if r.get("status") == "unknown_item"), user["id"], ts))
    audit(user, "ESTIMATE_IMPORTED", "projects", pid, {"created": len(created), "skipped": len(skipped)})
    return {"ok": True, "created": created, "skipped": skipped, "project": project_stats(pid)}


ROOM_HINT_RE = re.compile(r"\b(room|hall|cabin|office|class|classroom|toilet|washroom|corridor|stair|lobby|store|"
                          r"lab|laboratory|library|quarter|kitchen|terrace|shaft|panel room)\b", re.I)


def _auto_rooms(pid: int, raw_line: str) -> None:
    """If the estimate row mentions a location, create the room so the checklist can group by it."""
    for m in ROOM_HINT_RE.finditer(raw_line or ""):
        start = max(0, m.start() - 18)
        label = raw_line[start:m.end() + 22].strip(" ,.;:-")
        label = re.sub(r"\s+", " ", label)
        if len(label) < 4:
            continue
        label = label[:48]
        if not q1("SELECT id FROM rooms WHERE project_id=? AND lower(name)=lower(?)", (pid, label)):
            order = (q1("SELECT COALESCE(MAX(sort_order),0) AS m FROM rooms WHERE project_id=?", (pid,))["m"] or 0) + 1
            ex("INSERT INTO rooms (project_id, floor, name, sort_order, created_at) VALUES (?,'',?,?,?)",
               (pid, label, order, now_iso()))
        return


@app.get("/api/projects/{pid}/checklist")
def checklist(pid: int, room_id: Optional[int] = None, user: dict = Depends(current_user)):
    """Mobile measurement checklist derived from the estimate, with per-item progress."""
    if not q1("SELECT id FROM projects WHERE id=?", (pid,)):
        raise HTTPException(404, "Project not found")
    items = rows_to_dicts(q("""SELECT pi.id, pi.item_code, pi.description, pi.unit, pi.rate, pi.tendered_qty,
                                      pi.is_non_schedule, pi.source, pi.master_item_id,
                                      COALESCE(SUM(m.measured_qty),0) AS measured_qty,
                                      COUNT(m.id) AS rows_count
                               FROM project_items pi LEFT JOIN measurements m
                                 ON m.project_item_id = pi.id AND (? IS NULL OR m.room_id = ?)
                               WHERE pi.project_id = ?
                               GROUP BY pi.id ORDER BY pi.sort_order, pi.id""", (room_id, room_id, pid)))
    rooms = rows_to_dicts(q("""SELECT r.*, COALESCE(SUM(m.measured_qty),0) AS qty,
                                      COUNT(DISTINCT m.project_item_id) AS items_touched,
                                      COUNT(m.id) AS rows_count
                               FROM rooms r LEFT JOIN measurements m ON m.room_id = r.id
                               WHERE r.project_id = ? GROUP BY r.id ORDER BY r.sort_order, r.id""", (pid,)))
    for it in items:
        it["measured_qty"] = round(it["measured_qty"] or 0, 3)
        it["pending_qty"] = round((it["tendered_qty"] or 0) - it["measured_qty"], 3)
        it["done"] = it["measured_qty"] > 0
        it["fully_measured"] = it["tendered_qty"] and it["measured_qty"] >= it["tendered_qty"] - 1e-6
    return {"rooms": rooms, "items": items,
            "totals": {"items": len(items), "done": sum(1 for i in items if i["done"]),
                       "fully": sum(1 for i in items if i["fully_measured"])}}


@app.get("/api/projects/{pid}/deviations")
def deviations(pid: int, user: dict = Depends(current_user)):
    rows = deviation_rows(pid)
    p = dict(q1("SELECT * FROM projects WHERE id=?", (pid,)))
    return {
        "project": p, "rows": rows,
        "totals": {
            "tendered_amount": round(sum(r["tendered"] * r["rate"] for r in rows), 2),
            "measured_amount": round(sum(r["measured"] * r["rate"] for r in rows), 2),
            "excess_amount": round(sum(r["dev_amount"] for r in rows if r["dev_amount"] > 0), 2),
            "saving_amount": round(sum(r["dev_amount"] for r in rows if r["dev_amount"] < 0), 2),
            "net_amount": round(sum(r["dev_amount"] for r in rows), 2),
            "critical": [r for r in rows if r["severity"] == "critical"],
            "review": [r for r in rows if r["severity"] == "review"],
        },
    }


# =============================================================== MEASUREMENTS
@app.post("/api/measurements")
def create_measurement(body: MeasurementIn, user: dict = Depends(current_user)):
    it = q1("""SELECT pi.*, p.id AS pid FROM project_items pi JOIN projects p ON p.id = pi.project_id
               WHERE pi.id = ?""", (body.project_item_id,))
    if not it:
        raise HTTPException(404, "Project item not found")
    qty = body.measured_qty if body.measured_qty is not None else compute_qty(
        it["unit"], body.nos, body.length, body.breadth, body.height)
    ts = now_iso()
    mid = ex("""INSERT INTO measurements (project_id, project_item_id, room_id, length, breadth, height, nos,
                measured_qty, notes, measured_by, measured_on, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?, 'submitted', ?)""",
             (it["pid"], it["id"], body.room_id, body.length, body.breadth, body.height,
              body.nos, qty, body.notes, body.measured_by or user["id"],
              body.measured_on or datetime.now().strftime("%Y-%m-%d"), ts)).lastrowid
    audit(user, "MEASUREMENT_RECORDED", "measurements", mid,
          {"item": it["item_code"], "qty": qty, "room": body.room_id})
    total = q1("SELECT COALESCE(SUM(measured_qty),0) AS q FROM measurements WHERE project_item_id=?",
               (body.project_item_id,))["q"]
    return {"ok": True, "measurement_id": mid, "measured_qty": qty,
            "item_total_qty": round(total, 3), "tendered_qty": it["tendered_qty"],
            "deviation": round(total - (it["tendered_qty"] or 0), 3)}


@app.get("/api/projects/{pid}/measurements")
def list_measurements(pid: int, room_id: Optional[int] = None, user: dict = Depends(current_user)):
    sql = """SELECT m.*, pi.item_code, pi.description, pi.unit, pi.rate, r.name AS room_name, r.floor,
                    u.name AS measured_by_name,
                    (SELECT COUNT(*) FROM measurement_photos ph WHERE ph.measurement_id = m.id) AS photos
             FROM measurements m
             JOIN project_items pi ON pi.id = m.project_item_id
             LEFT JOIN rooms r ON r.id = m.room_id
             LEFT JOIN users u ON u.id = m.measured_by
             WHERE m.project_id = ?"""
    params: list[Any] = [pid]
    if room_id:
        sql += " AND m.room_id = ?"
        params.append(room_id)
    sql += " ORDER BY m.created_at DESC, m.id DESC"
    rows = rows_to_dicts(q(sql, params))
    for r in rows:
        r["amount"] = round((r["measured_qty"] or 0) * (r["rate"] or 0), 2)
    return {"measurements": rows, "count": len(rows),
            "total_amount": round(sum(r["amount"] for r in rows), 2)}


@app.patch("/api/measurements/{mid}")
def patch_measurement(mid: int, body: dict, user: dict = Depends(current_user)):
    row = q1("SELECT * FROM measurements WHERE id=?", (mid,))
    if not row:
        raise HTTPException(404, "Measurement not found")
    allowed = {"length", "breadth", "height", "nos", "measured_qty", "notes", "room_id", "status", "measured_on"}
    sets, params = [], []
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise HTTPException(400, "Nothing to update")
    params.append(mid)
    ex(f"UPDATE measurements SET {', '.join(sets)} WHERE id=?", params)
    audit(user, "MEASUREMENT_UPDATED", "measurements", mid, body)
    return {"ok": True}


@app.delete("/api/measurements/{mid}")
def delete_measurement(mid: int, user: dict = Depends(current_user)):
    for ph in q("SELECT filename FROM measurement_photos WHERE measurement_id=?", (mid,)):
        try:
            os.remove(os.path.join(PHOTO_DIR, ph["filename"]))
        except OSError:
            pass
    ex("DELETE FROM measurements WHERE id=?", (mid,))
    audit(user, "MEASUREMENT_DELETED", "measurements", mid)
    return {"ok": True}


@app.post("/api/measurements/{mid}/photos")
async def upload_photo(mid: int, file: UploadFile = File(...), caption: str = Form(""),
                       user: dict = Depends(current_user)):
    if not q1("SELECT id FROM measurements WHERE id=?", (mid,)):
        raise HTTPException(404, "Measurement not found")
    ext = os.path.splitext(file.filename or ".jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
        raise HTTPException(400, "Only image files can be attached as site evidence")
    name = f"m{mid}_{uuid.uuid4().hex[:10]}{ext}"
    with open(os.path.join(PHOTO_DIR, name), "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    pid_ = ex("INSERT INTO measurement_photos (measurement_id, filename, caption, uploaded_at) VALUES (?,?,?,?)",
              (mid, name, caption, now_iso())).lastrowid
    audit(user, "PHOTO_UPLOADED", "measurements", mid, name)
    return {"ok": True, "photo_id": pid_, "url": f"/api/photos/{name}"}


@app.get("/api/measurements/{mid}/photos")
def list_photos(mid: int, user: dict = Depends(current_user)):
    rows = rows_to_dicts(q("SELECT * FROM measurement_photos WHERE measurement_id=? ORDER BY id", (mid,)))
    for r in rows:
        r["url"] = f"/api/photos/{r['filename']}"
    return {"photos": rows}


@app.get("/api/photos/{filename}")
def serve_photo(filename: str, user: dict = Depends(current_user)):
    safe = os.path.basename(filename)
    path = os.path.join(PHOTO_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "Photo not found")
    return FileResponse(path)


# =================================================================== REPORTS
def _form23_data(pid: int) -> tuple[dict, list[dict], dict]:
    p = q1("SELECT p.*, u.name AS engineer_name, u.designation FROM projects p LEFT JOIN users u ON u.id=p.engineer_id"
           " WHERE p.id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    items = rows_to_dicts(q("""SELECT pi.*, mi.category, mi.spec_no, mi.short_desc AS master_short
                               FROM project_items pi LEFT JOIN master_items mi ON mi.id = pi.master_item_id
                               WHERE pi.project_id=? ORDER BY pi.sort_order, pi.id""", (pid,)))
    for it in items:
        it["short_desc"] = it.get("master_short")
        meas = rows_to_dicts(q("""SELECT m.*, r.name AS room_name, r.floor FROM measurements m
                                  LEFT JOIN rooms r ON r.id=m.room_id
                                  WHERE m.project_item_id=? ORDER BY m.id""", (it["id"],)))
        for i, m in enumerate(meas, start=1):
            m["sub"] = i
            m["amount"] = round((m["measured_qty"] or 0) * (it["rate"] or 0), 2)
        it["measurements"] = meas
        it["measured_qty"] = round(sum(m["measured_qty"] or 0 for m in meas), 3)
    profile = {"engineer": f"{p['engineer_name'] or ''} ({p['designation'] or 'Engineer'})",
               "period": _measurement_period(pid)}
    return dict(p), items, profile


def _measurement_period(pid: int) -> str:
    row = q1("SELECT MIN(measured_on) AS a, MAX(measured_on) AS b FROM measurements WHERE project_id=?", (pid,))
    if not row or not row["a"]:
        return "-"
    return f"{reports.fmt_date(row['a'])} to {reports.fmt_date(row['b'])}"


@app.get("/api/projects/{pid}/form23.pdf")
def form23_pdf(pid: int, measured_only: bool = False, user: dict = Depends(current_user)):
    p, items, profile = _form23_data(pid)
    pdf = reports.form23_pdf(p, items, profile, measured_only=measured_only)
    audit(user, "FORM23_GENERATED", "projects", pid, {"format": "pdf", "items": len(items)})
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="Form23_MB_{p.get("mb_no") or pid}.pdf"'})


@app.get("/api/projects/{pid}/form23.xlsx")
def form23_xlsx(pid: int, user: dict = Depends(current_user)):
    p, items, profile = _form23_data(pid)
    xlsx = reports.form23_xlsx(p, items, profile)
    audit(user, "FORM23_GENERATED", "projects", pid, {"format": "xlsx", "items": len(items)})
    return Response(xlsx, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="Form23_MB_{p.get("mb_no") or pid}.xlsx"'})


# ===================================================================== ADMIN
@app.get("/api/admin/overview")
def admin_overview(user: dict = Depends(admin_only)):
    def one(sql):
        return list(dict(q1(sql)).values())[0]
    return {
        "users": rows_to_dicts(q("SELECT id, name, email, role, designation, division, circle, region, is_active,"
                                 " created_at, last_login FROM users ORDER BY role, name")),
        "stats": {
            "user_count": one("SELECT COUNT(*) FROM users"),
            "engineer_count": one("SELECT COUNT(*) FROM users WHERE role='engineer'"),
            "master_rows": one("SELECT COUNT(*) FROM master_items"),
            "versions": one("SELECT COUNT(*) FROM csr_versions"),
            "items_per_version": rows_to_dicts(q("SELECT fy, region, item_count, status, source_file, uploaded_at"
                                                 " FROM csr_versions ORDER BY fy DESC, region")),
            "project_count": one("SELECT COUNT(*) FROM projects"),
            "measurement_count": one("SELECT COUNT(*) FROM measurements"),
            "photo_count": one("SELECT COUNT(*) FROM measurement_photos"),
            "measurement_value": round(one("""SELECT COALESCE(SUM(m.measured_qty * pi.rate),0) FROM measurements m
                                              JOIN project_items pi ON pi.id=m.project_item_id"""), 2),
            "projects_by_region": rows_to_dicts(q("SELECT region, COUNT(*) AS count FROM projects GROUP BY region")),
            "chapter_mix": rows_to_dicts(q("""SELECT chapter, COUNT(*) AS count FROM master_items
                                              WHERE fy=? GROUP BY chapter ORDER BY chapter""", (seed.FYS[-1],))),
        },
        "audit": rows_to_dicts(q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 60")),
    }


@app.post("/api/admin/users")
def admin_create_user(body: dict, user: dict = Depends(admin_only)):
    email = (body.get("email") or "").strip()
    if not email or not body.get("name"):
        raise HTTPException(400, "Name and email are required")
    if q1("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)):
        raise HTTPException(409, "Email already registered")
    uid = ex("INSERT INTO users (name,email,password_hash,role,designation,division,circle,region,phone,is_active,"
             "created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
             (body["name"], email, auth.hash_password(body.get("password") or "Welcome@123"),
              body.get("role", "engineer"), body.get("designation", ""), body.get("division", ""),
              body.get("circle", ""), body.get("region", "Pune"), body.get("phone", ""), now_iso())).lastrowid
    audit(user, "USER_CREATED", "users", uid, email)
    return {"ok": True, "user_id": uid, "default_password": body.get("password") or "Welcome@123"}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(uid: int, body: dict, user: dict = Depends(admin_only)):
    allowed = {"role", "is_active", "designation", "division", "circle", "region", "name", "phone"}
    sets, params = [], []
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if "password" in body and body["password"]:
        sets.append("password_hash=?")
        params.append(auth.hash_password(body["password"]))
    if not sets:
        raise HTTPException(400, "Nothing to update")
    params.append(uid)
    ex(f"UPDATE users SET {', '.join(sets)} WHERE id=?", params)
    audit(user, "USER_UPDATED", "users", uid, {k: v for k, v in body.items() if k != "password"})
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, user: dict = Depends(admin_only)):
    if uid == user["id"]:
        raise HTTPException(400, "You cannot delete your own account")
    target = q1("SELECT * FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "User not found")
    if q1("SELECT COUNT(*) AS c FROM projects WHERE engineer_id=?", (uid,))["c"]:
        raise HTTPException(409, "This engineer owns projects — disable the account instead of deleting it")
    ex("DELETE FROM users WHERE id=?", (uid,))
    audit(user, "USER_DELETED", "users", uid, target["email"])
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(limit: int = 200, user: dict = Depends(admin_only)):
    return {"audit": rows_to_dicts(q("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 1000),)))}


@app.post("/api/admin/reseed")
def admin_reseed(master: bool = False, demo: bool = False, user: dict = Depends(admin_only)):
    out = {}
    if master:
        out["master"] = seed.seed_master_csr(user)
    if demo:
        pid = seed.seed_demo_project()
        out["demo_project_id"] = pid
    audit(user, "RESEED", "system", "", out)
    return {"ok": True, **out}


@app.get("/api/admin/parse-jobs")
def parse_jobs(user: dict = Depends(admin_only)):
    return {"jobs": rows_to_dicts(q("""SELECT pj.*, p.name AS project_name, p.project_code FROM parse_jobs pj
                                       LEFT JOIN projects p ON p.id=pj.project_id ORDER BY pj.id DESC LIMIT 100"""))}


# ================================================================ DASHBOARD
@app.get("/api/dashboard")
def dashboard(user: dict = Depends(current_user)):
    scope = "" if user["role"] == "admin" else "WHERE p.engineer_id = %d" % user["id"]
    projects = rows_to_dicts(q(f"""SELECT p.*, u.name AS engineer_name FROM projects p
                                   LEFT JOIN users u ON u.id=p.engineer_id {scope}
                                   ORDER BY p.created_at DESC LIMIT 8"""))
    for p in projects:
        agg = q1("""SELECT COUNT(*) AS items, COALESCE(SUM(tendered_qty*rate),0) AS tendered
                    FROM project_items WHERE project_id=?""", (p["id"],))
        meas = q1("""SELECT COALESCE(SUM(x.qty*x.rate),0) AS amt FROM (
                        SELECT pi.id, pi.rate, COALESCE(SUM(m.measured_qty),0) AS qty FROM project_items pi
                        LEFT JOIN measurements m ON m.project_item_id=pi.id WHERE pi.project_id=?
                        GROUP BY pi.id) x""", (p["id"],))["amt"]
        p["items"] = agg["items"]
        p["tendered_amount"] = round(agg["tendered"], 2)
        p["measured_amount"] = round(meas, 2)
        p["progress_pct"] = round(meas / agg["tendered"] * 100, 1) if agg["tendered"] else 0.0
    scope_meas = "" if user["role"] == "admin" else f"WHERE p.engineer_id = {user['id']}"
    totals = q1(f"""SELECT COUNT(*) AS projects FROM projects p {scope}""")
    mv = q1(f"""SELECT COUNT(*) AS measurements FROM measurements m JOIN projects p ON p.id=m.project_id
                {('WHERE p.engineer_id = %d' % user['id']) if user['role'] != 'admin' else ''}""")
    value = q1(f"""SELECT COALESCE(SUM(m.measured_qty*pi.rate),0) AS amt FROM measurements m
                   JOIN project_items pi ON pi.id=m.project_item_id
                   JOIN projects p ON p.id=m.project_id
                   {('WHERE p.engineer_id = %d' % user['id']) if user['role'] != 'admin' else ''}""")["amt"]
    recent = rows_to_dicts(q(f"""SELECT m.id, m.measured_qty, m.measured_on, pi.item_code, pi.description, pi.unit,
                                        pi.rate, r.name AS room_name, p.name AS project_name, p.id AS project_id
                                 FROM measurements m JOIN project_items pi ON pi.id=m.project_item_id
                                 JOIN projects p ON p.id=m.project_id LEFT JOIN rooms r ON r.id=m.room_id
                                 {('WHERE p.engineer_id = %d' % user['id']) if user['role'] != 'admin' else ''}
                                 ORDER BY m.id DESC LIMIT 12"""))
    by_cat = rows_to_dicts(q("""SELECT COALESCE(mi.category,'Non-Schedule') AS category,
                                       COUNT(DISTINCT pi.id) AS items,
                                       COALESCE(SUM(x.qty*pi.rate),0) AS amount
                                FROM project_items pi
                                LEFT JOIN master_items mi ON mi.id=pi.master_item_id
                                LEFT JOIN (SELECT project_item_id, SUM(measured_qty) AS qty FROM measurements
                                           GROUP BY project_item_id) x ON x.project_item_id=pi.id
                                JOIN projects p ON p.id=pi.project_id
                                {scope}
                                GROUP BY category ORDER BY amount DESC""".replace(
        "{scope}", ("WHERE p.engineer_id = %d" % user["id"]) if user["role"] != "admin" else "")))
    return {
        "user": user_public(user),
        "totals": {"projects": totals["projects"], "measurements": mv["measurements"],
                   "measured_value": round(value, 2)},
        "projects": projects, "recent_measurements": recent, "by_category": by_cat,
        "csrfy": seed.FYS[-1],
    }


@app.get("/api/health")
def health():
    return {"ok": True, "time": now_iso(), "items": q1("SELECT COUNT(*) AS c FROM master_items")["c"],
            "projects": q1("SELECT COUNT(*) AS c FROM projects")["c"]}


# ------------------------------------------------------------------ static app
@app.get("/api/samples/{name}")
def sample_file(name: str):
    safe = os.path.basename(name)
    path = os.path.join(SAMPLE_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "Sample not found")
    return FileResponse(path, filename=safe)


if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
