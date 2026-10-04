"""Import the official PWD Electrical CSR PDF into the Master DB.

The printed CSR is a fixed table: item code at the left edge, description in the wide
column, one unit token just before the money columns, then three rates (completed rate,
+5% rates, +10% rates).  Codes look like 1-1-1 and may carry a suffix letter (1-3-3d).
Chapter and sub-chapter headings ("Chapter : 7  CABLES  (CB)", "7.2 Laying of cables
(CB-LY)") carry the specification number, which is what makes an item traceable.

    python3 -m tools.import_csr <pdf> --fy 2022-23 --region Maharashtra [--csv out.csv]
    python3 -m tools.import_csr samples/csr_2022-23_maharashtra.csv --from-csv --db-import

Two outputs, both deterministic:
  * a CSV (the canonical, reviewable form — committed to the repo so a deployed
    instance can seed itself without shipping the 1.5 MB PDF),
  * rows in master_items + a csr_versions record for the FY/region.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parsing import (CSR_CODE_RE, parse_csr_pdf, parse_csr_csv, tags_for,  # noqa: F401
                         write_csr_csv as to_csv)


def from_csv(path: str) -> list[dict]:
    return parse_csr_csv(path)


def parse_pdf(path: str) -> list[dict]:
    return parse_csr_pdf(path)


def import_rows(items: list[dict], fy: str, region: str, source: str) -> dict:
    """Write the parsed CSR into master_items as one FY/region version."""
    from app import db
    from app.db import ex, json as _  # noqa: F401  (json import kept explicit below)
    import json as _json

    db.init_db()
    ts = db.now_iso()
    existing = db.q1("SELECT COUNT(*) AS c FROM master_items WHERE fy=? AND region=?", (fy, region))["c"]
    if existing:
        ex("DELETE FROM master_items WHERE fy=? AND region=?", (fy, region))
    seen: set[str] = set()
    kept = 0
    for it in items:
        code = (it.get("item_code") or "").strip().lower()
        desc = _clean(it.get("description") or "")
        if not code or code in seen or len(desc) < 8:
            continue
        seen.add(code)
        unit = _clean(it.get("unit") or "") or "Each"
        ex("""INSERT OR REPLACE INTO master_items (fy, region, item_code, description, short_desc, unit, rate,
                 material_rate, labour_rate, chapter, section, category, spec_no, tags, is_new, is_active,
                 created_at, updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
           (fy, region, code, desc, (desc[:117] + "…") if len(desc) > 120 else desc, unit,
            float(it.get("rate") or 0), 0.0, 0.0, it.get("chapter"), _clean(it.get("section") or ""),
            _clean(it.get("category") or ""), _clean(it.get("spec_no") or ""),
            _json.dumps(tag_of(desc)), 0, 1, ts, ts))
        kept += 1
    ex("""INSERT INTO csr_versions (fy, region, status, item_count, source_file, notes, uploaded_at)
          VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(fy, region) DO UPDATE SET status='active', item_count=excluded.item_count,
              source_file=excluded.source_file, notes=excluded.notes, uploaded_at=excluded.uploaded_at""",
       (fy, region, "active", kept, source,
        "Official PWD Electrical CSR imported from the printed PDF (completed rate + 5%/10% rates).", ts))
    ex("""INSERT INTO audit_log (user_id, user_name, action, entity, entity_id, detail, created_at)
          VALUES (NULL, 'system', 'CSR_IMPORTED', 'master_items', ?, ?, ?)""",
       (f"{fy} {region}", _json.dumps({"items": kept, "source": source, "replaced": existing}), ts))
    return {"items": kept, "replaced": existing, "fy": fy, "region": region}


def main() -> None:
    ap = argparse.ArgumentParser(description="Import the printed PWD Electrical CSR")
    ap.add_argument("source", help="CSR PDF, or a CSV produced earlier")
    ap.add_argument("--fy", default="2022-23")
    ap.add_argument("--region", default="Maharashtra")
    ap.add_argument("--csv", help="write the parsed table to this CSV")
    ap.add_argument("--from-csv", action="store_true", help="source is a CSV, not a PDF")
    ap.add_argument("--db-import", action="store_true", help="write into master_items")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    items = from_csv(args.source) if args.from_csv else parse_pdf(args.source)
    if args.limit:
        items = items[:args.limit]
    with_rates = sum(1 for i in items if i["rate"])
    with_unit = sum(1 for i in items if i.get("unit"))
    print(f"parsed {len(items)} item rows · {with_rates} with a completed rate · {with_unit} with a unit")
    if not items:
        sys.exit("nothing parsed - check the file")

    if args.csv:
        to_csv(items, args.csv)
        print(f"csv  -> {args.csv}")
    if args.db_import:
        out = import_rows(items, args.fy, args.region, os.path.basename(args.source))
        print(f"db   -> {out}")


if __name__ == "__main__":
    main()
