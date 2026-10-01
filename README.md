# Smart-MB — Centralized CSR Database & Site Verification System

**Maharashtra Public Works Department (Electrical Wing) · PWD Electrical Engineers**

<!-- CI: the workflow is shipped as docs/ci-workflow.yml - copy it to .github/workflows/ci.yml
     from the GitHub web UI ("Add file" -> paste) to run the suite on every push. -->
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Mukesh931/smart-mb)

**Live demo:** <https://smart-mb.onrender.com> — sign in as `admin@pwd.maharashtra.gov.in` / `Admin@123`
or `je.nashik@pwd.maharashtra.gov.in` / `Engineer@123`. *(Free Render instance: it sleeps after 15 idle
minutes and cold-starts in ~1 minute; see the free-tier note in [`docs/DEPLOY.md`](docs/DEPLOY.md) before
using it for real data — the free plan has no persistent disk, so the SQLite file resets on
spin-down/redeploy.)*

A mobile-first platform where the **Master Electrical CSR (Schedule of Rates)** lives once, in a
database controlled by the Super Admin, and every project **references** it. Site engineers upload a
Technical Sanction estimate, the platform maps it against the master, builds a measurement checklist,
records joint measurements at site, and prints the official **Form No. 23 (Measurement Book)** — with
descriptions and rates that are always the clean legal text from the Master CSR, never OCR noise from
a blurry PDF.

Alongside the estimate, the platform also reads the **Descriptive Schedule** — the room / floor /
location-wise quantity statement of the building — and reconciles it against the estimate. At site the
engineer then walks **room by room**: quantities that match the descriptive schedule are *kept* with
one tap, and only the ones that differ are *changed* to the actual figure. Every change writes a
measurement, so Form-23 and the deviation statement follow automatically. See
[`docs/DESCRIPTIVE_SCHEDULE.md`](docs/DESCRIPTIVE_SCHEDULE.md).

```
        ADMIN                                    SITE ENGINEER
┌────────────────────────┐            ┌──────────────────────────────────────┐
│ Master CSR 2023-24     │            │ 1. New project                       │
│ Master CSR 2024-25     │  Smart     │ 2. Upload estimate (PDF/Excel/paste) │
│ item_code · desc ·     │  Map       │ 3. Reconciliation preview → confirm  │
│ unit · rate · tags     │ ─────────▶ │ 4. Room-wise checklist on mobile     │
│ 7 regions × N FY       │            │ 5. Joint measurements + photos       │
└────────────────────────┘            │ 6. Extra items pulled from CSR       │
                                      │ 7. Form-23 MB PDF/Excel + deviations │
                                      └──────────────────────────────────────┘
```

---

## 1 · Quick start

```bash
cd smart-mb
./run.sh                 # installs deps, seeds the database, serves on http://localhost:8000
```

or manually:

```bash
pip install -r requirements.txt
python3 -m tools.make_samples          # builds the sample estimate files + CSR import template
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** (mobile-friendly; can be added to the home screen).

### Demo accounts

| Role | Email | Password |
|---|---|---|
| **Super Admin** (Master CSR control, users, audit) | `admin@pwd.maharashtra.gov.in` | `Admin@123` |
| **Site Engineer** (Nashik) | `je.nashik@pwd.maharashtra.gov.in` | `Engineer@123` |
| Site Engineer (Pune) | `je.pune@pwd.maharashtra.gov.in` | `Engineer@123` |
| Site Engineer (Nagpur) | `je.nagpur@pwd.maharashtra.gov.in` | `Engineer@123` |

The login screen has one-tap fill buttons for both roles. A seeded demo project
(*Zilla Parishad High School, Sinnar*) already carries 44 estimate items and 147 joint measurements so
every screen — deviations, progress, Form-23 — is populated on first login.

### Verification

```bash
./run.sh test            # or: python3 -m tools.smoke_test http://127.0.0.1:8000
```

The smoke test drives every flow end-to-end (auth → project → parse messy PDF → reconciliation →
checklist → measurements → extra items → deviations → **descriptive schedule parse / import / room-wise
keep-or-change verification / control-sheet export** → Form-23 PDF/Excel → admin CSR import → audit →
cleanup) and cleans up every artifact it created. **72/72 API checks pass**, plus a headless UI harness
(`node tools/ui_test.mjs`) that renders every screen — including the room verification sheet and the
column-linking sheet — against the demo snapshot: **22/22 pass**.

---

## 2 · What is implemented

### 2.1 Master CSR (Super Admin, one-time / annual)

* Full **Master Database** of the Electrical CSR with `item_code`, legal description, short
  description, unit, **material / labour / completed rate**, chapter, section, category, specification
  number and **metadata tags** (`Fan`, `Earthing`, `LED`, `Safety`, …).
* Ships with a **demo subset of 163 items across 8 chapters** (1 Wiring · 2 Light Fittings · 3 Fans ·
  5 HT & Substation · 6 DB & Switchgear · 7 Cables · 9 Earthing · 14 Temporary & Misc), item codes in
  the official three-part form (`1-3-6`, `2-1-11`, `9-1-4`, `14-3-1`), materialised for
  **2 financial years (2023-24, 2024-25) × 7 regions** = **2 268 rate rows**, with region factors
  (Pune 1.00, Mumbai 1.06, Nagpur 0.97, Nashik 1.02, …). Replace it with the official FY file in one
  upload — the schema matches the published CSR column layout.
* **Two-step import**: *Preview & validate → Commit*. Column detection is alias-driven (with exact-match
  priority so a *Material Rate* column is never mistaken for the *Completed Rate*), the header row is
  auto-located, malformed rows are reported with spreadsheet row numbers, and each version is tracked
  (`source_file`, `uploaded_at`, `item_count`, `status`).
* **Export** any FY × region back to Excel; **tag editor** per item; **audit trail** for every change.

### 2.2 Project creation & the “Smart Import” (anchor-based extraction)

`app/parsing.py` implements the strategy from the brief, hardened for real PWD files:

1. **Text layer** — `pypdf` in layout mode (keeps column gaps), `openpyxl` for Excel, CSV for
   plain data; if the text layer is sparse the file is flagged as a scan and an OCR shim plus
   OCR-repair pass is applied.
2. **Anchor pass** — `^<digits>-<digits>-<digits>$` anywhere in the stream, with OCR digit
   substitution (`O→0`, `l→1`, `S→5`, `B→8`) and sanity bounds on chapter/section/item.
3. **Quantity pass** — four cascading strategies, each recorded with a method + confidence:
   `unit-anchored` (unit cell followed only by whitespace then the quantity — 0.96) →
   `unit-wrapped` (unit at end of row, quantity on the next line — 0.66) →
   `header-column` (quantity column band detected from the header row — 0.90) →
   `row-end-numeric` (last non-specification number on the row — 0.84, downgraded to 0.66 when the row
   carries several numeric columns) → `continuation-line` (0.55). Specification numbers
   (`1.5 sq.mm`, `20 mm dia`, `1200 x 300`) are excluded from quantity candidates.
4. **OCR repair of codes** — a code that misses the master lookup is healed **only at the character
   positions that were printed as letters** (`1-O-1 → 1-0-1 → 1-1-1`), and the surviving candidates are
   ranked by *description similarity* with the printed row, so a blurry scan still lands on the exact
   legal description. A cleanly printed code that simply is not in the CSR is **never guessed at** —
   it is reported as `Unknown_Item`, exactly as the specification requires.
5. **Master lookup** — descriptions, units and rates are taken from the **Master CSR**; the PDF text is
   used *only* for codes that are not in the database (non-schedule fallback).
6. **Reconciliation preview** — `Found N anchors (M unique codes). X matched the Master CSR, Y items are
   Unknown_Item`, with per-row badges, flags, confidence, provenance line numbers, editable quantities
   and editable descriptions/rates for non-schedule rows, plus rooms auto-detected from the estimate
   text. Confirming generates the **measurement checklist**.

### 2.3 The Descriptive Schedule — room-wise reconciliation & verification

The descriptive schedule is where the building's quantities actually live: **locations run down the
sheet, work items run across the top** (the column headings are printed rotated 90° on the PWD
proforma). `app/schedule.py` reads it as a matrix and `app/schedule_store.py` reconciles it with the
project:

1. **Rotated-header matrix extraction** (`pymupdf` glyph directions — no OCR needed for digital PDFs).
   The 14 locations / 55 item columns of the sample sheet are recovered exactly, including a second
   `TOILET` and the `HALL` vs `MAIN HALL` distinction.
2. **Self-checked reading** — the printed `TOTAL OF …` / `GRAND TOTAL` row is compared with the sum of
   the parsed cells, column by column. On the sample sheet **55 of 55 columns reconcile to the paisa**;
   any column that does not is flagged in red before the engineer ever sees the grid.
3. **Header-driven, so it scales** — nothing is hard-coded to the sample's column set: a column map is
   never assumed. PDF (rotated **or** plain), scanned pages (paste mode / OCR-friendly rebuild),
   Excel, CSV and long-format `Location | Item | Unit | Qty` tables are all accepted, in any column
   order.
4. **Locations → project rooms** — exact-name matches reuse an existing room; anything new becomes a
   room (repeats get `TOILET (2)`), so measurements and the checklist line up with the sheet.
5. **Column → item mapping from the Master CSR** — IDF-weighted wording coverage picks the matching
   estimate line (`Ceiling Fan 1200 mm` → `3-1-1 … ceiling fan 1200 mm sweep`), preferring items
   already in the estimate; near-ties are marked **“confirm”** and the engineer picks from a one-tap
   shortlist, or links a Master CSR item / creates an extra item for work that is in the schedule but
   not in the estimate.
6. **Control sheet** — per item: **estimate quantity · schedule quantity · difference · actual so far ·
   rooms verified**, exportable to Excel (`…/reconciliation.xlsx`, with a second *Room wise* sheet).
7. **Room-wise verification at site** — the core loop the brief asked for. Inside a room each schedule
   quantity shows `Keep ✓` / `Actual` buttons:
   * **Keep** → confirmed as per the descriptive schedule (one tap, or “all as per schedule” for the
     whole room);
   * **Actual** → the measured figure replaces it, and a **measurement row is written** with the room,
     item, quantity and note, so the checklist, deviation statement and Form-23 stay in step;
   * entering an actual equal to the schedule is recorded as a *keep*, and any verification can be
     redone.
   Progress is tracked per room and per item (`x/y verified`, `n pending`), so the engineer sees
   exactly which rooms still need a visit.

### 2.4 Onsite measurement (mobile)

* Checklist grouped by **room / location**, derived from the estimate, showing tendered vs measured vs
  pending with progress bars and Excess / Complete badges.
* **Measurement capture** — No · Length · Breadth · Height entry with live quantity computation
  (unit-aware: linear, area, volume, countable), editable override, joint-measurement note, date, and
  **camera photo evidence** (stored and linked to the measurement record).
* **Extra-item logic** — search the Master CSR in natural language (“exhaust fan”, “chemical earthing”,
  “LED street light”); the ranking blends tag matches, code matches and description overlap; picking a
  result attaches the **correct item code, legal description, unit and rate** automatically. Items that
  genuinely are not in the CSR are captured as **non-schedule** items with a reason/approval reference.
* Room-wise measurement register with per-location values, photos, edit/delete, and an Excel MB export.

### 2.5 Reporting & validation

* **Form-23 (M.B.) PDF** — print-ready landscape A4: work particulars block, item-wise table in MB
  column format (No · L · B · H · Qty · Rate · Amount) with the **Master CSR legal description** and CSR
  code in the item head, location sub-entries per room, per-item totals, grand total, certification
  text and four signature blocks (Measured by JE · Checked by DE · Contractor representative ·
  Countersigned EE). Optional `?measured_only=true`.
* **Deviation statement** (page 2 of the PDF and a sheet in the Excel) — tendered vs measured per item,
  deviation quantity, %, and **Excess / Saving valued at Master CSR rates**, with critical items
  (≥ ±20 %) highlighted for variation approval.
* **Excel MB** — three sheets: `Form-23 Measurements`, `Deviation Statement`, `Estimate Items`
  (with match method + confidence for audit).
* Dashboard & project KPIs: tendered vs measured value, progress %, net deviation, excess/saving,
  value distribution by work head.

### 2.6 Admin governance

Master CSR versions & reseed · user management (create, role, region/division, enable/disable, password
reset) · **audit trail** of every master change, import, measurement and report · **parsing jobs**
register (file, engine, anchors, matched, unknown).

---

## 3 · Screens

| Route | Purpose |
|---|---|
| `#/dashboard` | Role-aware KPIs, works in hand with progress, value by work head, latest measurements |
| `#/csr` | Browse/search the Master CSR (FY, region, chapter, head, tag); admin import panel + export |
| `#/projects` | Project cards, progress, create-project wizard (with room list) |
| `#/project/:id` · Overview | Work particulars, rooms, deviation alerts, delete |
| `#/project/:id` · Smart Import | Upload/paste estimate → reconciliation table → confirm |
| `#/project/:id` · Site Verify | Descriptive schedule: rooms list → *keep / change* per room, “all as per schedule”, estimate-vs-schedule control sheet, schedule files |
| `#/project/:id` · Checklist | Room chips, item cards, Measure button, Extra item from CSR |
| `#/project/:id` · Measurements | Room-wise register, photos, edit/delete |
| `#/project/:id` · Deviations | Excess/saving statement with severity |
| `#/project/:id` · Form-23 & Reports | PDF/Excel generation, report readiness |
| `#/admin` | Master DB versions, users & roles, audit trail, parsing jobs |

*(Screens are aliasable — `/web/project.html?id=3` style links are unnecessary because the SPA is served
from `/` with a hash router; the FastAPI static mount also serves every file under `web/` directly.)*

---

## 4 · API surface

| Method | Endpoint | Notes |
|---|---|---|
| `POST` | `/api/auth/login` · `/register` · `GET /api/auth/me` | signed bearer tokens (HMAC), PBKDF2 passwords |
| `GET` | `/api/csr/versions` · `/facets` · `/items` · `/suggest` · `/export` | browse, filter, natural-language search |
| `POST` | `/api/csr/import` (multipart, `commit=false|true`) | two-step Master CSR import |
| `POST` | `/api/csr/items/{id}/tags` | admin tag editing |
| `GET/POST` | `/api/projects` · `GET/PATCH/DELETE /api/projects/{id}` | project CRUD + KPIs |
| `POST` | `/api/projects/{id}/parse-estimate` (file) · `/parse-text` | anchor-based parsing |
| `POST` | `/api/projects/{id}/import-estimate` | commit reconciliation → checklist |
| `POST` | `/api/projects/{id}/parse-schedule` (file) · `/parse-schedule-text` | descriptive schedule matrix (PDF/scan/Excel/CSV/paste) + printed-total check |
| `POST` | `/api/projects/{id}/import-schedule` | store the schedule, create the rooms, propose item links |
| `GET` | `/api/projects/{id}/schedules` · `/api/schedule-docs/{id}` | schedule documents & parsed matrix |
| `GET` | `/api/projects/{id}/reconciliation` · `/reconciliation.xlsx` | estimate vs schedule vs actual control sheet |
| `GET` | `/api/projects/{id}/verify` | room-major verification worklist |
| `POST` | `/api/schedule-cells/{id}/verify` | **keep** the schedule quantity · **change** to actual · undo |
| `POST` | `/api/schedule-docs/{id}/verify-bulk` | one-tap “all as per schedule” for a room / item |
| `POST` | `/api/schedule-docs/{id}/columns/{n}/map` | link a column to an estimate / Master CSR item, or create an extra item |
| `GET` | `/api/projects/{id}/checklist` · `/deviations` · `/measurements` · `/items` | verification data |
| `POST` | `/api/projects/{id}/items` | extra item from CSR **or** non-schedule item |
| `POST/PATCH/DELETE` | `/api/measurements` · `/api/measurements/{id}` | joint measurement recording |
| `POST/GET` | `/api/measurements/{id}/photos` · `GET /api/photos/{file}` | site evidence |
| `GET` | `/api/projects/{id}/form23.pdf` · `form23.xlsx` | Form-23 MB generation |
| `GET` | `/api/admin/overview` · `/audit` · `/parse-jobs` · `POST /reseed` | governance |
| `GET` | `/api/dashboard` · `/api/health` | role-aware aggregates |

Interactive API docs: **http://localhost:8000/docs** (OpenAPI).

Report/photo links accept the session either as a bearer header or as `?token=…` so they can be
opened/previewed outside the SPA’s fetch layer.

---

## 5 · Data model (`app/db.py`, SQLite)

```
users               id, name, email, password_hash, role(admin|engineer), designation, division, circle, region, is_active
csr_versions        fy, region, status, item_count, source_file, uploaded_by, uploaded_at         UNIQUE(fy,region)
master_items        fy, region, item_code, description, short_desc, unit, rate,
                    material_rate, labour_rate, chapter, section, category, spec_no, tags[], is_new
                    UNIQUE(fy, region, item_code)          ← the permanent Master CSR
projects            project_code, name, scheme, division, circle, region, engineer_id, estimate_no,
                    ts_no, ts_date, ts_amount, csr_fy, csr_region, mb_no, agreement_no, agency, status
project_items       project_id → projects, master_item_id → master_items (nullable = non-schedule),
                    item_code, description, unit, rate, tendered_qty, is_non_schedule, ns_reason,
                    source(estimate|extra|manual), pdf_qty, confidence, match_method
rooms               project_id, floor, name, sort_order
measurements        project_id, project_item_id, room_id, length, breadth, height, nos, measured_qty,
                    notes, measured_by, measured_on, status
measurement_photos  measurement_id, filename, caption, uploaded_at
schedule_docs       project_id, filename, engine(rotated-matrix|grid-matrix|grid-long-table), orientation,
                    title, name_of_work, stats, warnings, raw_json, status
schedule_locations  doc_id, project_id, floor, name, sort_order, row_total, room_id → rooms, match_score
schedule_cells      doc_id, location_id, column_order, col_label, qty, project_item_id → project_items,
                    master_item_id, item_code, match_confidence, match_method,
                    verify_status(pending|kept|changed|not_applicable), actual_qty, measurement_id, note
parse_jobs          project_id, filename, file_type, engine, anchors, matched, unknown
audit_log           user_id, user_name, action, entity, entity_id, detail, created_at
```

SQLite is used for a zero-configuration single-file deployment; the schema maps 1:1 onto PostgreSQL for
a departmental rollout (swap `app/db.py` for SQLAlchemy/asyncpg — no API changes).

---

## 6 · Repository layout

```
smart-mb/
├── run.sh                     launcher (./run.sh | ./run.sh test)
├── requirements.txt
├── render.yaml                Render Blueprint (web service + disk + env vars)
├── Procfile · runtime.txt      generic PaaS start command / Python version pin
├── docs/DEPLOY.md             GitHub → Render deployment walkthrough
├── docs/DESCRIPTIVE_SCHEDULE.md   how the room-wise schedule is read, reconciled and verified
├── app/
│   ├── db.py                  schema + connection helpers + audit
│   ├── auth.py                PBKDF2 passwords, HMAC bearer tokens
│   ├── seed.py                demo Master CSR (163 items × FY × region), users, measured demo project
│   ├── parsing.py             anchor-based estimate parser, OCR repair, CSR importer, AI prompt
│   ├── schedule.py            descriptive-schedule reader: rotated-header matrix, grid/long tables, column→CSR matching
│   ├── schedule_store.py      schedule persistence, room creation, estimate-vs-schedule reconciliation, verification
│   ├── reports.py             Form-23 PDF (reportlab) + Excel MB (openpyxl), deviation statement
│   └── main.py                FastAPI: auth, csr, projects, measurements, reports, admin, dashboard
├── web/                       single-page front-end served at /
│   ├── index.html             app shell + embedded offline-demo snapshot
│   ├── style.css              design system (mobile-first, no external assets)
│   ├── app.js                 hash router, all screens, live API + offline demo fallback
│   └── smart-mb-standalone.html   one-file build (inline CSS+JS+data) for previews / tablets
├── samples/                   sample_estimate.pdf (deliberately messy) · .xlsx · .csv · CSR template
│                               descriptive_schedule_sample.pdf (real PWD proforma) · .xlsx · .csv
├── tools/
│   ├── make_samples.py        generates the sample estimate files + import template
│   ├── embed_demo.py          injects a read-only data snapshot into index.html
│   ├── smoke_test.py          72-check end-to-end API test of every flow
│   ├── ui_test.mjs            22-check headless render test of every screen
│   ├── build_standalone.py    single-file HTML build
│   └── deploy_all.py          scripted GitHub push + Render service creation (env-provided tokens)
├── .github/workflows/ci.yml   CI: seeds the DB, runs the 72-check API suite + 22-check UI harness
└── data/                      smartmb.sqlite3 · uploads/ · photos/   (created at runtime)
```

### The parsing-engine prompt

`app/parsing.py` exposes the exact prompt from the specification (`AI_PROMPT_TEMPLATE`) and the UI shows
it under *Smart Import ▸ View the parsing-engine prompt*. The production path is deterministic
(regex + column heuristics + master lookup, auditable and free); the prompt is kept as the contract for
the optional LLM fallback mode for pathological scans — enable it by pointing the parser at an API key
server-side. The deterministic engine already resolves the failure cases that motivated the fallback:

```
$ python3 -m tools.smoke_test         # excerpts
[4] parse the estimate (messy PDF)   anchors 20 · matched 18 · Unknown_Item 1
    OCR artefact cured (1-O-1 → 1-1-1) · duplicate anchor merged · descriptions from Master CSR
[9] Form-23 PDF 26 KiB / 6 pages     "as per specification" wording retained, deviation + signatures
```

---

### Continuous integration

The full suite (samples → seed → **72 API checks** → **22 UI checks** → standalone build assertion) is
defined in [`docs/ci-workflow.yml`](docs/ci-workflow.yml). GitHub only accepts workflow files from a
token with the `workflow` scope, so enable it in one click: **GitHub → your repo → Add file → Create new
file → path `.github/workflows/ci.yml` → paste the contents of `docs/ci-workflow.yml` → Commit.** Every
push then runs the same suite that `./run.sh test` runs locally.

---

## 7 · Run it on the open internet (GitHub + Render)

The repository ships deploy configs: `render.yaml` (Render Blueprint), `Procfile`,
`runtime.txt` and an env-driven `SMARTMB_DATA_DIR` so the database lives on a persistent disk.

```bash
git init -b main && git add . && git commit -m "Smart-MB platform"
git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main
```

Prefer not to touch git remotes? The same thing scripted (tokens read from the environment, never stored):

```bash
export GITHUB_TOKEN=ghp_...  RENDER_API_KEY=rnd_...
python3 -m tools.deploy_all all --repo-name smart-mb --visibility public --plan free
# → creates the repo, pushes main, creates the Render service, waits for "live", runs /api/health
```

Or in the dashboard: **New + → Blueprint → pick the repo → Apply**. Full walkthrough, environment
variables, free-tier caveats and a post-deploy checklist: **[`docs/DEPLOY.md`](docs/DEPLOY.md)**.

Quick check once it is live:

```bash
curl -s https://<your-service>.onrender.com/api/health
```

## 8 · Deploying for real

1. **Master data** — export the official CSR for each FY/region to Excel with the columns
   `Item No | Description | Unit | Material Rate | Labour Rate | Completed Rate | Category | Section | Spec No | Tags`
   (template available in-app under *Master CSR ▸ Download template*) and import it as Super Admin.
2. **Database** — replace SQLite with PostgreSQL; keep `master_items` unique on
   `(fy, region, item_code)` and consider an index on `GIN(tags)`.
3. **Secrets** — set `SMARTMB_SECRET` and serve behind HTTPS (tokens are HMAC-signed, 14-day TTL).
4. **Storage** — move `data/uploads` and `data/photos` to object storage; photos are served from
   `/api/photos/{file}` behind the session check.
5. **Offline** — the checklist and measurement capture work against the API; for true field offline
   mode, wrap `web/` in a service worker and queue `POST /api/measurements` in IndexedDB.

---

## 9 · Hosting it yourself

| | Free instance | Starter + 1 GB disk ($7/mo) |
|---|---|---|
| Cost | $0 | ~$7/month |
| Sleeps after 15 idle min | yes (≈1 min cold start) | no |
| SQLite DB, uploads, site photos | **lost on spin-down/redeploy** | persisted on the disk at `/var/data` |
| Suitable for | demos, evaluation | real field measurement work |

Deploy the durable variant (needs payment details on the Render account):

```bash
export GITHUB_TOKEN=... RENDER_API_KEY=...
python3 -m tools.deploy_all render --plan starter --disk   # adds a 1 GB disk at /var/data
python3 -m tools.deploy_all status                          # plan, disk, deploys, live health
```

For heavier use, point `app/db.py` at Render Postgres instead of SQLite (the schema maps 1:1).

## 10 · Notes & honest limitations

* The bundled CSR is a **demo subset** (163 items, representative rates) modelled on the published
  chapter/section/item-code structure of the Maharashtra PWD Electrical Schedule of Rates — replace it
  with the department’s live file; nothing in the app assumes the demo numbers.
* Scanned PDFs without a text layer need real OCR (Tesseract / cloud OCR). The app detects that case,
  applies an OCR shim, and marks quantities low-confidence — but a genuine scan should be OCR’d before
  upload for best results.
* Photo capture uses the browser file input with `capture="environment"`; HEIC/WEBP handling depends on
  the device browser.
* Form-23 layout follows the standard MB column structure but the departmental proforma should be
  reconciled once with the circle office before the first official bill.
* A rendered sample of the output is committed at `samples/Form23_MB_sample_output.pdf` (17 pages:
  measurement pages + deviation statement + signatures) and `.xlsx` (3 sheets).
