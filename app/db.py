"""
Smart-MB :: Database layer (SQLite)
Maharashtra PWD Electrical - Centralized CSR Database & Site Verification System

Schema mirrors the spec:
  users                -> Super Admin / Site Engineer
  csr_versions         -> Master CSR FY + region versions (Admin controlled)
  master_items         -> MasterCSR_Table  (item_code PRIMARY KEY per FY+region)
  projects             -> Site projects
  project_items        -> Project_Estimate_Table (FK -> master_items)
  rooms                -> Measurement locations
  measurements         -> Measurement_Table
  measurement_photos   -> site photo evidence
  parse_jobs           -> audit of every estimate parse
  audit_log            -> system audit trail
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Writable state (SQLite file, uploaded estimates, site photos).  Point this at a
# mounted persistent disk in production, e.g. SMARTMB_DATA_DIR=/var/data on Render.
DATA_DIR = os.environ.get("SMARTMB_DATA_DIR") or os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
# Read-only demo/reference files that ship with the repository
SAMPLE_DIR = os.path.join(BASE_DIR, "samples")
DB_PATH = os.environ.get("SMARTMB_DB_PATH") or os.path.join(DATA_DIR, "smartmb.sqlite3")

for _d in (DATA_DIR, UPLOAD_DIR, PHOTO_DIR, SAMPLE_DIR):
    os.makedirs(_d, exist_ok=True)

_local = threading.local()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    cur = get_conn().execute(sql, tuple(params))
    return cur.fetchone()


def ex(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = get_conn()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'engineer',     -- admin | engineer
    designation     TEXT,
    division        TEXT,
    circle          TEXT,
    region          TEXT DEFAULT 'Pune',
    phone           TEXT,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT,
    last_login      TEXT
);

CREATE TABLE IF NOT EXISTS csr_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fy          TEXT NOT NULL,                 -- e.g. 2024-25
    region      TEXT NOT NULL,                 -- Pune / Nagpur / ...
    status      TEXT DEFAULT 'active',         -- active | archived
    item_count  INTEGER DEFAULT 0,
    source_file TEXT,
    notes       TEXT,
    uploaded_by INTEGER,
    uploaded_at TEXT,
    UNIQUE (fy, region)
);

CREATE TABLE IF NOT EXISTS master_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fy            TEXT NOT NULL,
    region        TEXT NOT NULL,
    item_code     TEXT NOT NULL,               -- 1-1-1 ... 7-4-2
    description   TEXT NOT NULL,
    short_desc    TEXT,
    unit          TEXT NOT NULL,
    rate          REAL NOT NULL DEFAULT 0,
    material_rate REAL DEFAULT 0,
    labour_rate   REAL DEFAULT 0,
    chapter       INTEGER,
    section       TEXT,
    category      TEXT,                        -- Mains / Internal / Earthing / Lighting ...
    spec_no       TEXT,
    tags          TEXT DEFAULT '[]',            -- JSON list, e.g. ["Fan","Earthing"]
    is_new        INTEGER DEFAULT 0,
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT,
    UNIQUE (fy, region, item_code)
);
CREATE INDEX IF NOT EXISTS idx_master_code ON master_items (fy, region, item_code);
CREATE INDEX IF NOT EXISTS idx_master_cat  ON master_items (category);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code  TEXT UNIQUE,
    name          TEXT NOT NULL,
    scheme        TEXT,
    division      TEXT,
    circle        TEXT,
    region        TEXT DEFAULT 'Pune',
    engineer_id   INTEGER REFERENCES users(id),
    estimate_no   TEXT,
    ts_no         TEXT,
    ts_date       TEXT,
    ts_amount     REAL DEFAULT 0,
    csr_fy        TEXT,
    csr_region    TEXT,
    mb_no         TEXT,
    agreement_no  TEXT,
    agency        TEXT,
    status        TEXT DEFAULT 'active',       -- active | completed
    parse_summary TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS project_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    master_item_id INTEGER REFERENCES master_items(id),
    item_code      TEXT NOT NULL,
    description    TEXT NOT NULL,
    unit           TEXT,
    rate           REAL DEFAULT 0,
    tendered_qty   REAL DEFAULT 0,
    is_non_schedule INTEGER DEFAULT 0,
    ns_reason      TEXT,
    source         TEXT DEFAULT 'estimate',    -- estimate | extra | manual
    pdf_qty        REAL,
    confidence     REAL DEFAULT 0,
    match_method   TEXT,
    sort_order     INTEGER DEFAULT 0,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pitems_project ON project_items (project_id);

CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    floor       TEXT,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS measurements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    project_item_id INTEGER NOT NULL REFERENCES project_items(id) ON DELETE CASCADE,
    room_id         INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    length          REAL DEFAULT 0,
    breadth         REAL DEFAULT 0,
    height          REAL DEFAULT 0,
    nos             REAL DEFAULT 1,
    measured_qty    REAL NOT NULL DEFAULT 0,
    notes           TEXT,
    measured_by     INTEGER REFERENCES users(id),
    measured_on     TEXT,
    status          TEXT DEFAULT 'submitted',   -- submitted | approved | rejected
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_meas_project ON measurements (project_id);

CREATE TABLE IF NOT EXISTS measurement_photos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    measurement_id INTEGER NOT NULL REFERENCES measurements(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    caption        TEXT,
    uploaded_at    TEXT
);

CREATE TABLE IF NOT EXISTS parse_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    filename     TEXT,
    file_type    TEXT,
    engine       TEXT,
    raw_text     TEXT,
    anchors      INTEGER DEFAULT 0,
    matched      INTEGER DEFAULT 0,
    unknown      INTEGER DEFAULT 0,
    result_json  TEXT,
    created_by   INTEGER,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    user_name  TEXT,
    action     TEXT,
    entity     TEXT,
    entity_id  TEXT,
    detail     TEXT,
    created_at TEXT
);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def audit(actor: dict | None, action: str, entity: str = "", entity_id: Any = "",
          detail: Any = "") -> None:
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, default=str)
    ex(
        "INSERT INTO audit_log (user_id, user_name, action, entity, entity_id, detail, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            (actor or {}).get("id"),
            (actor or {}).get("name") or "system",
            action,
            entity,
            str(entity_id) if entity_id is not None else "",
            str(detail)[:4000],
            now_iso(),
        ),
    )


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default
