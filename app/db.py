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
        # FastAPI runs endpoints on a thread pool and several writers must queue rather
        # than fail: the busy timeout goes on first, before anything that takes a lock.
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # WAL is a *persistent* database property.  Re-requesting it on every new
            # connection needs an exclusive lock and deadlocks against another process
            # holding a write (two workers, a script plus the server, a smoke test).
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass                                             # already WAL (or busy): fine
        _local.conn = conn
    return conn


_LOCK_RETRIES = 6


def _with_retry(fn, sql: str, params: Iterable[Any]):
    """Run a statement, waiting out a concurrent writer instead of erroring."""
    import sqlite3 as _sq
    import time as _t
    delay = 0.05
    for attempt in range(_LOCK_RETRIES):
        try:
            return fn(sql, params)
        except _sq.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == _LOCK_RETRIES - 1:
                raise
            _t.sleep(delay)
            delay = min(delay * 2, 1.0)


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    cur = get_conn().execute(sql, tuple(params))
    return cur.fetchone()


def ex(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    def _run(sql_: str, params_: Iterable[Any]) -> sqlite3.Cursor:
        conn = get_conn()
        cur = conn.execute(sql_, tuple(params_))
        conn.commit()
        return cur

    return _with_retry(_run, sql, params)


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

CREATE TABLE IF NOT EXISTS org_units (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER REFERENCES org_units(id),
    kind        TEXT NOT NULL,                 -- division | subdivision | section
    name        TEXT NOT NULL,
    code        TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT,
    UNIQUE (parent_id, name)
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
    client_ref      TEXT,                       -- phone-generated id: makes offline replay idempotent
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

CREATE TABLE IF NOT EXISTS schedule_docs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename     TEXT,
    engine       TEXT,                    -- pymupdf-rotated-matrix | grid-matrix | grid-long-table ...
    orientation  TEXT,                    -- locations_rows | locations_cols
    title        TEXT,
    name_of_work TEXT,
    estimate_no  TEXT,
    stats        TEXT,                    -- JSON  {columns, locations, cells, columns_reconciled ...}
    warnings     TEXT,                    -- JSON list
    raw_json     TEXT,                    -- full parse payload (audit / re-import)
    status       TEXT DEFAULT 'imported', -- imported | verified
    created_by   INTEGER,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sched_docs_project ON schedule_docs (project_id);

CREATE TABLE IF NOT EXISTS schedule_locations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     INTEGER NOT NULL REFERENCES schedule_docs(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL,
    floor      TEXT,
    name       TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    row_total  REAL DEFAULT 0,
    room_id    INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    match_score REAL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sched_loc_doc ON schedule_locations (doc_id);

CREATE TABLE IF NOT EXISTS schedule_cells (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id           INTEGER NOT NULL REFERENCES schedule_docs(id) ON DELETE CASCADE,
    project_id       INTEGER NOT NULL,
    location_id      INTEGER NOT NULL REFERENCES schedule_locations(id) ON DELETE CASCADE,
    column_order     INTEGER NOT NULL,
    col_label        TEXT NOT NULL,
    qty              REAL DEFAULT 0,
    project_item_id  INTEGER REFERENCES project_items(id) ON DELETE SET NULL,
    master_item_id   INTEGER REFERENCES master_items(id) ON DELETE SET NULL,
    item_code        TEXT,
    match_confidence REAL DEFAULT 0,
    match_method     TEXT,
    verify_status    TEXT DEFAULT 'pending',   -- pending | kept | changed | not_applicable
    actual_qty       REAL,
    measurement_id   INTEGER REFERENCES measurements(id) ON DELETE SET NULL,
    note             TEXT,
    checked_by       INTEGER,
    checked_at       TEXT,
    UNIQUE (doc_id, location_id, column_order)
);
CREATE INDEX IF NOT EXISTS idx_sched_cells_doc ON schedule_cells (doc_id);
CREATE INDEX IF NOT EXISTS idx_sched_cells_item ON schedule_cells (project_item_id);

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


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations - older databases keep working after an upgrade."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(measurements)")}
    if cols and "client_ref" not in cols:
        conn.execute("ALTER TABLE measurements ADD COLUMN client_ref TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meas_client ON measurements (client_ref, project_id)")

    # organisation: every user and every project sits somewhere in the division tree
    for table in ("users", "projects"):
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if tcols and "org_unit_id" not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN org_unit_id INTEGER REFERENCES org_units(id)")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if ucols and "section" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN section TEXT")
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if pcols and "section" not in pcols:
        conn.execute("ALTER TABLE projects ADD COLUMN section TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_org ON projects (org_unit_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_org ON users (org_unit_id)")
    conn.commit()


def init_db() -> None:
    conn = get_conn()
    try:
        # a fresh database: set the journal mode once, here, where there is a single writer
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)


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
