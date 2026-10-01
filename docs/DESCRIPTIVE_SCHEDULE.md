# Descriptive Schedule — reading it, reconciling it, verifying room by room

The **estimate abstract** says *how much* of each item the work carries. The **descriptive
schedule** says *where* those quantities sit — floor by floor, room by room, location by location.
Smart-MB reads both and reconciles them, so the joint measurement visit only has to settle the
places where the two disagree.

```
        ESTIMATE (abstract)                 DESCRIPTIVE SCHEDULE                SITE
   item_code · qty · unit · rate      locations ↓   ·   items →            measured quantities
   ┌──────────────────────────┐      ┌───────────────────────────────┐    ┌──────────────────┐
   │ 3-1-1  ceiling fan   16  │ ───▶ │            HALL  MAIN HALL    │───▶│ keep  (matches)  │
   │ 1-9-1  light point   84  │      │ 1-9-1  ...  9        20       │    │ change (differs) │
   │ 2-1-3  LED batten    96  │      │ 3-1-1  ...  4         6       │    └──────────────────┘
   └──────────────────────────┘      └───────────────────────────────┘             │
                ▲                                  │                               │
                └────────── reconciliation ────────┴──────── measurement rows ─────┘
```

## 1 · What the file looks like

The PWD proforma is printed landscape with the **column headings rotated 90°** and the locations
running down the left-hand side:

```
        ┌ Descriptiue Schedule ────────────────────────────────────────────────┐
        │ Name Of Work :- … Estimate No. 291 of 2025-26 …                       │
        ├──────────────┬───────────────────────────────────────────────────────┤
        │ Item         │  C  A  C  P  A  …  55 item columns, rotated headings  │
        │ Description  │  o  d  o  l  .  …                                      │
        ├──────────────┼───────────────────────────────────────────────────────┤
        │(GROUND FLOOR)│                                                       │
        │ ENTRANCE     │  7  4  1  …                                           │
        │ PASSAGE-1    │  3     1  …                                           │
        │ …            │                                                       │
        │ MAIN HALL    │ 20  4  6  …                                           │
        │ TOTAL OF …   │ 64  4 11  …      ← printed totals, used as a check    │
        │ GRAND TOTAL  │ 64  4 11  …                                           │
        └──────────────┴───────────────────────────────────────────────────────┘
```

Nothing about that layout is guaranteed: the column set changes with the scale of the estimate
(a small building may have 12 columns, a substation 80), and the sheet may arrive as PDF, a scan,
Excel or CSV.

## 2 · How Smart-MB reads it (`app/schedule.py`)

1. **Text with writing direction.** `pymupdf` `rawdict` gives every span its bbox **and** its
   direction, so the 55 rotated headings are recovered as text — no OCR needed for a digital PDF.
   A plain `pypdf` text dump loses them completely (“Rotated text discovered”).
2. **Columns from the rotated spans**, clustered on their x-position; multi-line headings are
   rejoined top-to-bottom.
3. **Locations from the left-hand labels** above the first data column; `(GROUND FLOOR)` style
   brackets become the floor of the following rows; rows matching `TOTAL` / `TOTAL OF …` /
   `GRAND TOTAL` are pulled out as the printed total row instead of a location.
4. **Values mapped to the nearest column centre** (values are left- or right-aligned inside a
   ~12 pt column pitch, so exact x-equality is wrong) and to the nearest location row.
5. **Self-check.** Every item column's parsed cells are summed and compared with the printed
   `GRAND TOTAL`. On the reference sheet **55/55 columns reconcile exactly**. Columns that do not
   are flagged (that is how a mis-detected column is caught before it reaches the site).
6. **Other shapes** are handled by the same entry point:
   * Excel / CSV **matrix** (locations on either axis — the sheet is transposed when needed),
   * Excel / CSV **long table** — `Location | Item | Unit | Qty` in any column order,
   * **paste mode** for a scan the engineer transcribes by hand (mobile fallback),
   * scanned PDF → flagged, with instructions, instead of silently guessing.

## 3 · Reconciliation (`app/schedule_store.py`)

* **Locations → rooms.** Exact name match reuses the project room; otherwise the room is created.
  Repetitions are kept distinct (`TOILET`, `TOILET (2)`) and lookalikes are *not* merged
  (`HALL` ≠ `MAIN HALL`, `TOILET` ≠ `TOILET PASSAGE`).
* **Columns → items.** Matching is IDF-weighted wording coverage over the estimate's own items
  first (the estimate is the contract), then the Master CSR; near-ties are marked **confirm** and
  the engineer picks from a one-tap shortlist. A column that is not in the estimate can be linked to
  a Master CSR item or turned into an **extra item** with a reason.
* **Control sheet.** Per item: estimate qty · schedule qty · difference (and its value at CSR rates)
  · actual measured · rooms verified, exportable to Excel (`/api/projects/{id}/reconciliation.xlsx`,
  second sheet *Room wise*).

## 4 · The verification loop (mobile)

Inside a room each schedule quantity offers two actions:

| Action | Meaning | What the server does |
|---|---|---|
| **✓ As per schedule** | the schedule is what is actually there | cell marked `kept`; a measurement row is written with the schedule quantity |
| **Actual** | the site differs — enter the measured figure | cell marked `changed`; a measurement row is written with the **actual**, carrying the room, item and note |
| **All as per schedule** | whole room matches | every pending cell in the room is kept in one request |
| **Re-do** | undo a verification | cell returns to `pending` and the auto-written measurement is removed |

Because a *change* writes a real measurement, everything downstream moves with it: the checklist
progress, the deviation statement (excess/saving at CSR rates) and Form-23. Entering an actual that
equals the schedule is recorded as a *keep* — no phantom deviation.

## 5 · API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/projects/{id}/parse-schedule` | multipart file → parsed matrix + printed-total check + proposed mapping (nothing stored) |
| `POST` | `/api/projects/{id}/parse-schedule-text` | same, from pasted text |
| `POST` | `/api/projects/{id}/import-schedule` | store document, create rooms, apply confirmed links |
| `GET` | `/api/projects/{id}/schedules` · `/api/schedule-docs/{id}` | documents, locations, cells |
| `GET` | `/api/projects/{id}/reconciliation[.xlsx]` | control sheet |
| `GET` | `/api/projects/{id}/verify` | room-major worklist (schedule vs actual per cell) |
| `POST` | `/api/schedule-cells/{id}/verify` | `keep` · `change` · `not_applicable` · `pending` |
| `POST` | `/api/schedule-docs/{id}/verify-bulk` | whole room / whole item in one tap |
| `POST` | `/api/schedule-docs/{id}/columns/{n}/map` | link a column to a project / Master CSR item, or create an extra item |

## 6 · Known limits (honest list)

* **Digital PDFs are exact; scans are not.** A scanned schedule has no rotated glyphs to read, so the
  platform asks for the Excel/CSV or the pasted table rather than inventing quantities. (Rotated-heading
  OCR is the next step if scans are common in a division.)
* **Ambiguous short headings stay manual.** A column called just “Earthing” or “Switch” matches several
  CSR lines; the platform proposes a shortlist and asks, because guessing would corrupt room-wise
  quantities. Expect 20–40 % of columns of a typical sheet to need one confirmation tap the first
  time — the mapping is stored per project afterwards.
* **Column meaning is taken as-is.** The two decimal columns of the reference sheet (“Conduit Light/
  fan/bell point”, “Additional points”) are reconciled like any other column; if a division uses them
  as metres rather than points, set the unit when linking.
* **One schedule per project screen.** Multiple documents are supported and selectable, but
  verification runs against the newest by default.
