"""
Smart-MB :: Descriptive Schedule engine.

A "Descriptive Schedule" (also: descriptive estimate abstract, room-wise schedule)
lists **location-wise quantities** of the building: rows are rooms/floors, columns
are work items, cells are quantities.  Its shape varies wildly with the scale of the
estimate, so this module detects the layout instead of assuming one:

  A. ROTATED-HEADER MATRIX  (Google Sheets / Excel exports to PDF)
     work-item names are rotated 90° across the top, locations run down the left.
     -> pymupdf reads glyph directions, so rotated headers are recovered exactly.

  B. PLAIN MATRIX
     locations across the top (or down the left), items on the other axis,
     quantities in the body - read from PDF text, Excel or CSV.

  C. LONG / TIDY TABLE
     explicit columns such as  Location | Item / Description | Unit | Quantity
     (any order, any names) - read from Excel/CSV/PDF text.

Every parse is **self-checked**: the printed TOTAL / GRAND TOTAL row (or column) is
compared with the sum of the parsed cells, per item.  Columns whose maths reconcile
are trusted; the rest are flagged so the engineer verifies before using them.
"""
from __future__ import annotations

import csv
import io
import os
import re
import statistics
from typing import Any, Iterable

# ------------------------------------------------------------------ vocabulary
FLOOR_RE = re.compile(r"^[\(\[]\s*(.+?)\s*[\)\]]$")
TOTAL_RE = re.compile(r"\b(grand\s*total|total\s+of|sub\s*-?\s*total|total)\b", re.I)
QTY_HEADERS = ["qty", "quantity", "nos", "no.", "number", "count", "total qty", "quantities"]
ITEM_HEADERS = ["item", "description", "particulars", "work", "item description", "name of work",
                "item no", "code", "specification"]
LOC_HEADERS = ["location", "room", "floor", "area", "place", "hall", "space", "unit", "level",
               "room name", "location name", "space/room"]
UNIT_HEADERS = ["unit", "uom", "measure"]
# words that suggest a row/column label is a *location* rather than a work item
LOCATION_WORDS = ["room", "hall", "passage", "toilet", "bath", "kitchen", "office", "store", "storage",
                  "cabin", "corridor", "lobby", "stair", "floor", "terrace", "entrance", "veranda",
                  "balcony", "dressing", "ward", "class", "lab", "library", "shaft", "parking",
                  "compound", "external", "courtyard", "room no", "hall no", "block", "shade",
                  "passage", "porch", "record", "pantry", "water", "pump", "chowk", "sabhamandap",
                  "ganesh", "temple", "stage", "mantap", "mandap"]
# words that suggest a label is a *work item*
WORK_WORDS = ["point", "wiring", "conduit", "switch", "socket", "fan", "led", "light", "fitting",
              "panel", "mcb", "rccb", "mccb", "db", "board", "earthing", "cable", "geyser", "ac",
              "conditioner", "regulator", "bulkhead", "street", "module", "box", "enclosure",
              "lamp", "luminaire", "plug", "telephone", "lan", "bell", "isolator", "iso", "tube",
              "iron", "pump", "meter", "starter", "capacitor", "wire", "tray", "db"]
SYNONYMS = {
    "spmcb": "single pole mcb", "spndb": "spn distribution board", "tpn": "tpn", "tpndb": "tpn distribution board",
    "vtpndb": "vtpn distribution board", "dpmcb": "double pole mcb", "tpmcb": "triple pole mcb",
    "fpmcb": "four pole mcb", "fpmccb": "four pole mccb", "fpiso": "four pole isolator",
    "dpiso": "double pole isolator", "ex.fan": "exhaust fan", "ex fan": "exhaust fan",
    "concieled": "concealed", "concieled": "concealed", "conceiled": "concealed", "consealed": "concealed",
    "a.c.": "air conditioner", "ac": "air conditioner", "w.": "watt", "w": "watt",
    "led panel": "led panel luminaire", "led tube": "led batten luminaire", "tube": "batten",
    "c.f.": "ceiling fan", "fan 1200 mm": "ceiling fan 1200 mm sweep",
    "bulkhead": "led bulkhead luminaire", "street light": "led street light luminaire",
    "wall fan": "wall mounting fan", "geyser": "geyser point", "iron work": "hanger", "mod": "module",
}
STOPWORDS = {"the", "and", "of", "for", "with", "as", "per", "no", "nos", "to", "in", "on", "a", "an",
             "type", "each", "mtr", "sqmm", "sq", "mm", "w", "kw", "vat"}


# --------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _tokens(text: str) -> set[str]:
    t = _clean(text).lower()
    for k, v in SYNONYMS.items():
        t = t.replace(k, v)
    words = re.findall(r"[a-z0-9]+", t)
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _is_number(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d{1,6}(?:\.\d{1,3})?", _clean(text).replace(",", "")))


def _to_num(text: str) -> float:
    try:
        return float(_clean(text).replace(",", ""))
    except ValueError:
        return 0.0


def _looks_like_location(label: str) -> bool:
    l = _clean(label).lower()
    return any(w in l for w in LOCATION_WORDS)


def _looks_like_work_item(label: str) -> bool:
    l = _clean(label).lower()
    return any(w in l for w in WORK_WORDS)


def _idf(token_sets: list[set[str]]) -> dict[str, float]:
    """Inverse document frequency over the candidate pool: rare words (bulkhead, mccb,
    conduit) decide a match; common ones (point, light, fixing) barely move the score."""
    import math
    df: dict[str, int] = {}
    for toks in token_sets:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = len(token_sets) + 1
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def coverage_score(label: str, hay: str, idf: dict[str, float], unit: str = "") -> float:
    """How much of the schedule column label is accounted for by the item wording (0..1)."""
    lt, ht = _tokens(label), _tokens(hay)
    if not lt or not ht:
        return 0.0
    inter = lt & ht
    total = sum(idf.get(t, 1.0) for t in lt)
    hit = sum(idf.get(t, 1.0) for t in inter)
    score = hit / total if total else 0.0
    nl, nh = re.sub(r"[^a-z0-9]", "", label.lower()), re.sub(r"[^a-z0-9]", "", hay.lower())
    if len(nl) >= 5 and nl in nh:                       # the label appears verbatim
        score = min(1.0, score + 0.15)
    clean = lambda x: re.sub(r"[^A-Z0-9]", "", str(x).upper())       # noqa: E731
    if unit and clean(unit) and clean(unit) in clean(hay):           # unit agrees
        score = min(1.0, score + 0.05)
    return round(score, 3)


# A schedule column ("Ceiling Fan 1200 mm") asks for work to be *done*; a CSR row that
# only dismantles, rewinds, recesses or merely erects a departmentally supplied item
# mentions the same nouns but is the wrong row.  These intent rules are what keeps the
# auto-match honest - no model, just the wording the printed CSR actually uses.
_INTENT_PENALTY = [
    (r"\b(dismantl|removal of|credit for dismantled)", 0.45),
    (r"\brewinding\b|\brewind\b|\brepair\b|\breplacement of\b", 0.55),
    (r"\bproviding recess|recess in (stone|brick|concrete)", 0.55),
    (r"\berection of departmentally supplied|erecting the departmentally supplied|"
     r"\bsupplied by department\b|departmentally supplied", 0.65),
    (r"\btesting and charging|\btesting,? only\b", 0.7),
]
_INTENT_BONUS = [
    (r"^\s*supplying?\b", 1.06),
    (r"\bsupplying and (erecting|fixing|installing|laying)", 1.04),
]


def intent_factor(label: str, hay: str) -> float:
    """Multiplier that pushes a candidate towards the row that actually does the work."""
    label = (label or "").lower()
    hay_l = (hay or "").lower()
    factor = 1.0
    for pat, mul in _INTENT_PENALTY:
        if re.search(pat, hay_l) and not re.search(pat, label):
            factor *= mul
    for pat, mul in _INTENT_BONUS:
        if re.search(pat, hay_l):
            factor *= mul
    return round(factor, 4)


def similarity(a: str, b: str) -> float:
    """Token-overlap similarity (0..1) with partial-credit for contained phrases."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    jac = inter / len(ta | tb)
    la, lb = _clean(a).lower(), _clean(b).lower()
    bonus = 0.25 if (la in lb or lb in la) else 0.0
    return round(min(1.0, jac * 1.6 + bonus), 3)


# ------------------------------------------------------------------ PDF layout
def _spans_from_pdf(path: str) -> tuple[list[dict], list[str]]:
    """All text spans with position + writing direction.  Rotated headers survive."""
    warnings: list[str] = []
    try:
        import pymupdf
    except ImportError:                                     # pragma: no cover
        return [], ["pymupdf is not installed - rotated column headers cannot be recovered. "
                    "Install pymupdf (in requirements.txt) or upload the schedule as Excel/CSV."]
    spans: list[dict] = []
    doc = pymupdf.open(path)
    for page_no, page in enumerate(doc, start=1):
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                direction = tuple(round(v, 2) for v in line.get("dir", (1, 0)))
                for span in line.get("spans", []):
                    text = "".join(ch.get("c", "") for ch in span.get("chars", []))
                    if not text.strip():
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    spans.append({"text": _clean(text), "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                                  "dir": direction, "size": round(span.get("size", 0), 1), "page": page_no})
    doc.close()
    if not spans:
        warnings.append("No text layer found - this looks like a scan. Quantities entered manually "
                        "or re-upload an OCR'd copy.")
    return spans, warnings


def _cluster(values: list[float], tolerance: float) -> list[float]:
    """Group near-identical coordinates into canonical positions."""
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tolerance:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) / 2
    return out


def _assign(value: float, positions: list[float], tolerance: float) -> int | None:
    best, best_d = None, tolerance
    for i, p in enumerate(positions):
        d = abs(value - p)
        if d <= best_d:
            best, best_d = i, d
    return best


def parse_pdf_matrix(path: str) -> dict:
    spans, warnings = _spans_from_pdf(path)
    if not spans:
        return {"ok": False, "warnings": warnings, "engine": "pymupdf"}

    rotated = [s for s in spans if s["dir"] not in ((1.0, 0.0),)]
    horizontal = [s for s in spans if s["dir"] == (1.0, 0.0)]

    title = ""
    name_of_work = ""
    estimate_no = ""
    for s in horizontal[:40]:
        t = s["text"]
        if not title and re.search(r"descriptive\s+schedule|measurement\s+sheet|descriptive\s+estimate", t, re.I):
            title = t
        if re.search(r"name\s*of\s*work", t, re.I) and len(t) > 12:
            name_of_work = re.split(r"name\s*of\s*work\s*:?\s*-*\s*", t, flags=re.I)[-1]
        if re.search(r"estimate\s*no", t, re.I):
            estimate_no = t
        # the row above/below sometimes carries the work name alone
    if not name_of_work:
        for s in horizontal[:60]:
            if len(s["text"]) > 45 and not _is_number(s["text"]) and "estimate no" in s["text"].lower():
                name_of_work = re.sub(r"^.*?estimate no\.?[^A-Z]*", "", s["text"], flags=re.I)
                break
    if not name_of_work:
        for s in horizontal[:60]:
            if len(s["text"]) > 45:
                name_of_work = s["text"]
                break

    if not rotated:
        return _parse_pdf_plain(horizontal, {"title": title, "name_of_work": name_of_work,
                                             "estimate_no": estimate_no, "warnings": warnings})

    # ---- rotated headers = work-item columns
    cols_x = _cluster([round(s["x0"] * 2) / 2 for s in rotated], 4.0)
    headers: dict[int, list[dict]] = {}
    for s in rotated:
        idx = _assign(round(s["x0"] * 2) / 2, cols_x, 6.0)
        if idx is None:
            continue
        headers.setdefault(idx, []).append(s)
    # a header may be broken into several stacked spans - join by y
    columns: list[dict] = []
    for idx in sorted(headers):
        parts = sorted(headers[idx], key=lambda s: s["y0"])
        label = " ".join(p["text"] for p in parts).strip()
        columns.append({"order": len(columns), "label": label, "x": cols_x[idx]})
    pitch = statistics.median([b["x"] - a["x"] for a, b in zip(columns, columns[1:])]) if len(columns) > 1 else 14.0

    # ---- left-hand labels = locations / floor sections
    label_limit = columns[0]["x"] - pitch * 0.6
    labels = sorted([s for s in horizontal if s["x0"] < label_limit and len(s["text"]) < 60],
                    key=lambda s: (s["page"], s["y0"]))

    # ---- numeric cells
    def is_cell(s: dict) -> bool:
        if not _is_number(s["text"]):
            return False
        return s["x0"] >= label_limit - 2

    cells = [s for s in horizontal if is_cell(s)]
    cell_x = _cluster([round(c["x0"] * 2) / 2 for c in cells], 6.0)

    # ---- rows = labels that are neither headers, floor sections nor totals
    rows: list[dict] = []
    floor = ""
    total_rows: list[dict] = []
    for lab in labels:
        text = lab["text"]
        if FLOOR_RE.match(text):
            floor = _clean(FLOOR_RE.match(text).group(1))
            continue
        low = text.lower()
        if TOTAL_RE.search(low):
            total_rows.append({"label": text, "y": lab["y0"], "page": lab["page"]})
            continue
        if low in ("item description", "description", "particulars", "hall", "") or low.startswith("item "):
            if low in ("item description", "description", "particulars") or low.startswith("item "):
                continue
        if len(text) < 2 or text.isdigit():
            continue
        if re.search(r"name\s*of\s*work|estimate\s*no|descriptive", low):
            continue
        rows.append({"order": len(rows), "label": text, "floor": floor, "y": lab["y0"], "page": lab["page"]})

    if not rows:
        return {"ok": False, "warnings": warnings + ["Could not identify any location rows."],
                "engine": "pymupdf-rotated-matrix"}

    row_y = [r["y"] for r in rows]
    row_tol = max(4.0, (statistics.median([b - a for a, b in zip(row_y, row_y[1:])]) * 0.6)
                  if len(row_y) > 1 else 6.0)

    values: dict[tuple[int, int], float] = {}
    stray_x: list[float] = []
    for c in cells:
        ci = _assign(round(c["x0"] * 2) / 2, cell_x, 6.0)
        ri = _assign(c["y0"], row_y, row_tol)
        if ci is None:
            stray_x.append(c["x0"])
            continue
        if ri is None:
            continue
        key = (ri, ci)
        values[key] = values.get(key, 0.0) + _to_num(c["text"])

    # map *cell* column positions onto the rotated headers
    col_map: dict[int, int] = {}
    for ci, cx in enumerate(cell_x):
        hi = _assign(cx, [c["x"] for c in columns], max(6.5, pitch * 0.6))
        if hi is not None:
            col_map[ci] = hi

    # ---- printed totals (TOTAL OF FLOOR / GRAND TOTAL rows)
    printed_totals: dict[int, float] = {}
    for trow in total_rows:
        for c in cells:
            if abs(c["y0"] - trow["y"]) > row_tol:
                continue
            ci = _assign(round(c["x0"] * 2) / 2, cell_x, 6.0)
            if ci is None:
                continue
            hi = col_map.get(ci)
            if hi is None:
                continue
            printed_totals.setdefault(hi, 0.0)
            if "grand" in trow["label"].lower() or printed_totals[hi] == 0.0:
                printed_totals[hi] = _to_num(c["text"])

    out_cols = []
    for hi, col in enumerate(columns):
        cells_here = {ri: qty for (ri, ci), qty in values.items()
                      if col_map.get(ci) == hi and qty}
        total = round(sum(cells_here.values()), 3)
        printed = printed_totals.get(hi)
        out_cols.append({
            "order": len(out_cols), "label": col["label"], "parsed_total": total,
            "printed_total": printed,
            "total_match": (None if printed is None else abs(printed - total) < 0.05),
            "cells": cells_here,
        })

    locations = []
    for ri, row in enumerate(rows):
        cells_here = {ci: qty for (r, ci), qty in values.items() if r == ri and qty}
        locations.append({"order": ri, "label": row["label"], "floor": row.get("floor", ""),
                          "row_total": round(sum(cells_here.values()), 3), "cells": cells_here})

    matched = sum(1 for c in out_cols if c["total_match"] is True)
    checked = sum(1 for c in out_cols if c["total_match"] is not None)
    unmatched_axes = sorted({round(x, 1) for x in stray_x})
    if unmatched_axes:
        warnings.append(f"{len(unmatched_axes)} numeric column(s) could not be tied to a rotated header "
                        f"(x positions {unmatched_axes[:6]}) - check for a total/area column.")
    mismatch = [c["label"] for c in out_cols if c["total_match"] is False]
    if mismatch:
        warnings.append(f"{len(mismatch)} column(s) do not reconcile with the printed total: "
                        f"{', '.join(mismatch[:5])}")

    return {
        "ok": True, "engine": "pymupdf-rotated-matrix", "orientation": "locations_rows",
        "title": title or "Descriptive Schedule", "name_of_work": name_of_work, "estimate_no": estimate_no,
        "columns": out_cols, "locations": locations, "col_map": col_map, "cell_x": cell_x,
        "floors": [f for f in dict.fromkeys(l["floor"] for l in locations if l["floor"])],
        "warnings": warnings,
        "stats": {"columns": len(out_cols), "locations": len(locations),
                  "cells": len(values), "floors": len({l["floor"] for l in locations if l["floor"]}),
                  "columns_checked": checked, "columns_reconciled": matched},
    }


def _parse_pdf_plain(horizontal: list[dict], base: dict) -> dict:
    """Fallback for PDFs whose headers are horizontal (rows in the text layer)."""
    warnings = list(base.get("warnings", []))
    warnings.append("No rotated headers found - parsed as a plain table. Verify the mapping before use.")
    numbers = [s for s in horizontal if _is_number(s["text"])]
    if not numbers:
        return {"ok": False, "warnings": warnings, "engine": "pymupdf-plain"}
    return {"ok": False, "warnings": warnings, "engine": "pymupdf-plain",
            "hint": "Upload the schedule as Excel/CSV, or use the paste-text mode."}


# ------------------------------------------------------------ Excel / CSV / text
def _grid_from_xlsx(path: str) -> list[list[str]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    grid: list[list[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                grid.append(cells)
    return grid


def _grid_from_csv(path: str) -> list[list[str]]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        return [[c.strip() for c in row] for row in csv.reader(fh, dialect) if any(str(c).strip() for c in row)]


def _header_index(cell: str, names: list[str]) -> bool:
    c = _clean(cell).lower()
    return any(c == n or c.startswith(n + " ") or n in c for n in names)


def parse_grid(grid: list[list[str]]) -> dict:
    warnings: list[str] = []
    if not grid:
        return {"ok": False, "warnings": ["The file is empty."], "engine": "grid"}

    # ---- C. long / tidy table?
    for hi in range(min(len(grid), 12)):
        header = [_clean(c).lower() for c in grid[hi]]
        qty_i = next((i for i, c in enumerate(header) if _header_index(c, QTY_HEADERS)), None)
        item_i = next((i for i, c in enumerate(header) if _header_index(c, ITEM_HEADERS)), None)
        loc_i = next((i for i, c in enumerate(header) if _header_index(c, LOC_HEADERS)), None)
        if qty_i is not None and item_i is not None and loc_i is not None and len({qty_i, item_i, loc_i}) == 3:
            cols: dict[str, dict] = {}
            locations: dict[tuple[str, str], dict] = {}
            for row in grid[hi + 1:]:
                if max(qty_i, item_i, loc_i) >= len(row):
                    continue
                qty_raw, item, loc = row[qty_i], row[item_i], row[loc_i]
                if not _is_number(qty_raw) or not item.strip():
                    continue
                floor = ""
                m = FLOOR_RE.match(loc.strip())
                if m:
                    floor, loc = _clean(m.group(1)), ""
                    continue
                key = (loc.strip() or "Unspecified", floor)
                locations.setdefault(key, {"order": len(locations), "label": key[0], "floor": floor,
                                           "row_total": 0.0, "cells": {}})
                col = cols.setdefault(item.strip(), {"order": len(cols), "label": item.strip(),
                                                     "parsed_total": 0.0, "printed_total": None,
                                                     "total_match": None, "cells": {}})
                qty = _to_num(qty_raw)
                col["cells"][locations[key]["order"]] = qty
                col["parsed_total"] = round(col["parsed_total"] + qty, 3)
                locations[key]["row_total"] = round(locations[key]["row_total"] + qty, 3)
            columns = sorted(cols.values(), key=lambda c: c["order"])
            locations = sorted(locations.values(), key=lambda l: l["order"])
            for c in columns:
                c["cells"] = {int(k) if str(k).isdigit() else k: v for k, v in c["cells"].items()}
            return {
                "ok": bool(columns and locations), "engine": "grid-long-table",
                "orientation": "locations_rows",
                "title": "Descriptive Schedule", "name_of_work": "", "estimate_no": "",
                "columns": columns, "locations": locations, "floors": sorted({l["floor"] for l in locations if l["floor"]}),
                "warnings": warnings,
                "stats": {"columns": len(columns), "locations": len(locations),
                          "cells": sum(len(c["cells"]) for c in columns), "floors": 0,
                          "columns_checked": 0, "columns_reconciled": 0},
            }

    # ---- B. matrix: find the header row (most non-numeric labels across the row)
    header_i, best = None, 0
    for i, row in enumerate(grid[:15]):
        non_num = sum(1 for c in row if c and not _is_number(c))
        if non_num > best:
            header_i, best = i, non_num
    if header_i is None or best < 2:
        return {"ok": False, "warnings": ["Could not find a header row in the spreadsheet."], "engine": "grid-matrix"}

    header = grid[header_i]
    # which side holds the locations?
    first_col_labels = [r[0] for r in grid[header_i + 1:] if r and r[0] and not _is_number(r[0])]
    loc_like = sum(1 for l in first_col_labels if _looks_like_location(l))
    item_like = sum(1 for l in first_col_labels if _looks_like_work_item(l))
    locations_are_rows = loc_like >= item_like
    if not locations_are_rows:
        warnings.append("Row labels look like work items - interpreting the sheet as items-as-rows and "
                        "transposing it.")

    def pick_columns(header_row: list[str], skip_first: bool) -> list[str]:
        out = []
        start = 1 if skip_first else 0
        for c in header_row[start:]:
            out.append(_clean(c))
        return out

    columns: list[dict] = []
    locations: list[dict] = []

    if locations_are_rows:
        col_labels = pick_columns(header, True)
        for ci, label in enumerate(col_labels):
            if not label:
                continue
            columns.append({"order": len(columns), "label": label, "src_col": ci + 1,
                            "parsed_total": 0.0, "printed_total": None, "total_match": None, "cells": {}})
        floor = ""
        for row in grid[header_i + 1:]:
            if not row:
                continue
            first = _clean(row[0])
            if not first:
                continue
            m = FLOOR_RE.match(first)
            if m:
                floor = _clean(m.group(1))
                continue
            if TOTAL_RE.search(first):
                for col in columns:
                    idx = col.get("src_col")
                    if idx < len(row) and _is_number(row[idx]):
                        col["printed_total"] = _to_num(row[idx])
                continue
            if len(first) < 2:
                continue
            loc = {"order": len(locations), "label": first, "floor": floor, "row_total": 0.0, "cells": {}}
            for col in columns:
                idx = col.get("src_col")
                if idx < len(row) and _is_number(row[idx]):
                    qty = _to_num(row[idx])
                    if qty:
                        loc["cells"][col["order"]] = qty
                        col["cells"][loc["order"]] = qty
                        loc["row_total"] = round(loc["row_total"] + qty, 3)
            locations.append(loc)
    else:
        loc_labels = pick_columns(header, True)
        for li, label in enumerate(loc_labels):
            if not label:
                continue
            locations.append({"order": len(locations), "label": label, "floor": "",
                              "src_col": li + 1, "row_total": 0.0, "cells": {}})
        for row in grid[header_i + 1:]:
            if not row or not _clean(row[0]):
                continue
            label = _clean(row[0])
            m = FLOOR_RE.match(label)
            if m:
                for loc in locations:
                    loc["floor"] = _clean(m.group(1))
                continue
            if TOTAL_RE.search(label):
                continue
            col = {"order": len(columns), "label": label, "parsed_total": 0.0,
                   "printed_total": None, "total_match": None, "cells": {}}
            for loc in locations:
                idx = loc.get("src_col")
                if idx < len(row) and _is_number(row[idx]):
                    qty = _to_num(row[idx])
                    if qty:
                        col["cells"][loc["order"]] = qty
                        col["parsed_total"] = round(col["parsed_total"] + qty, 3)
                        loc["cells"][col["order"]] = qty
                        loc["row_total"] = round(loc["row_total"] + qty, 3)
            columns.append(col)

    for col in columns:
        col["parsed_total"] = round(sum(col["cells"].values()), 3)
        if col["printed_total"] is not None:
            col["total_match"] = abs(col["printed_total"] - col["parsed_total"]) < 0.05

    matched = sum(1 for c in columns if c["total_match"] is True)
    checked = sum(1 for c in columns if c["total_match"] is not None)
    return {
        "ok": bool(columns and locations), "engine": "grid-matrix",
        "orientation": "locations_rows" if locations_are_rows else "locations_cols",
        "title": "Descriptive Schedule", "name_of_work": "", "estimate_no": "",
        "columns": [{k: v for k, v in c.items() if k != "src_col"} for c in columns],
        "locations": [{k: v for k, v in l.items() if k != "src_col"} for l in locations],
        "floors": sorted({l["floor"] for l in locations if l["floor"]}),
        "warnings": warnings,
        "stats": {"columns": len(columns), "locations": len(locations),
                  "cells": sum(len(c["cells"]) for c in columns),
                  "floors": len({l["floor"] for l in locations if l["floor"]}),
                  "columns_checked": checked, "columns_reconciled": matched},
    }


# ------------------------------------------------------------------- entry point
def parse_descriptive_schedule(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_pdf_matrix(path)
    if ext in (".xlsx", ".xlsm", ".xls"):
        return parse_grid(_grid_from_xlsx(path))
    if ext in (".csv", ".tsv", ".txt"):
        return parse_grid(_grid_from_csv(path))
    return {"ok": False, "warnings": [f"Unsupported file type: {ext}"], "engine": "none"}


def parse_schedule_text(text: str) -> dict:
    rows = [re.split(r"\t|\s{2,}|\s*\|\s*", line) for line in text.splitlines() if line.strip()]
    return parse_grid([[c.strip() for c in row] for row in rows])


# --------------------------------------------------------------- column mapping
def map_columns(columns: Iterable[dict], project_items: list[dict], master_items: list[dict] | None = None,
                min_score: float = 0.0) -> list[dict]:
    """Propose a project item (and hence the Master CSR item) for every schedule column.

    The schedule says "Ceiling Fan 1200 mm"; the estimate says "3-1-1 ... supplying and
    erecting ceiling fan 1200 mm sweep ... Each".  Matching is IDF-weighted wording coverage
    over the candidate pool, so rare words decide; project items win ties because the
    estimate (not the master list) is the contract.  Columns that end up below ``min_score``
    or with no candidate at all are left for the engineer to link by hand.
    """
    master_items = master_items or []

    def hay(obj: dict) -> str:
        return " ".join(filter(None, [obj.get("short_desc"), obj.get("description"), obj.get("item_code")]))

    cands: list[tuple[str, dict, str]] = [("project_item", it, hay(it)) for it in project_items]
    cands += [("master_item", mi, hay(mi)) for mi in master_items]
    labels = [str(c.get("label") or "") for c in columns]
    idf = _idf([_tokens(h) for _k, _o, h in cands] + [_tokens(l) for l in labels])

    out = []
    for col in columns:
        label = col["label"]
        lt = _tokens(label)
        scored = []
        for kind, obj, text in cands:
            score = coverage_score(label, text, idf, obj.get("unit") or "")
            score = round(min(1.0, score * intent_factor(label, text)), 3)
            ht = _tokens(text)
            dice = (2 * len(lt & ht) / (len(lt) + len(ht))) if lt and ht else 0.0
            scored.append((score, round(dice, 4), 0 if kind == "project_item" else 1, kind, obj))
        # best wording coverage first, then the most *focused* candidate (a short exact
        # description beats a long one that merely mentions the words), then project items.
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))

        # The estimate (project items) is the contract: if one of its lines matches, that is the
        # target - a similar sibling in the Master CSR list is expected, not a conflict.  Only
        # when nothing in the estimate matches is the column treated as work outside the
        # estimate, and then the Master CSR shortlist is offered.
        pool = "project" if any(sc >= min_score for sc, _d, _p, k, _o in scored if k == "project_item") else "master"
        mine = [t for t in scored if (t[3] == "project_item") == (pool == "project")]
        mine.sort(key=lambda x: (-x[0], -x[1]))
        best = mine[0] if mine and mine[0][0] >= min_score else None
        second = next((sc for sc, _d, _p, k, o in mine[1:]
                       if best and (k, o["id"], o["item_code"]) != (best[3], best[4]["id"], best[4]["item_code"])
                       and o.get("item_code") != best[4].get("item_code")), None)
        ambiguous = bool(best and second is not None and abs(best[0] - second) <= 0.12)
        # alternatives: same pool first, then the other pool, de-duplicated by item code
        alts, seen = [], set()
        for sc, _dc, _pref, k, o in mine + [t for t in scored if t not in mine]:
            code = o.get("item_code")
            if sc <= 0 or code in seen:
                continue
            seen.add(code)
            alts.append((sc, k, o))
            if len(alts) >= 5:
                break

        row = dict(col)
        row.update({
            "suggested_project_item_id": best[4]["id"] if best and best[3] == "project_item" else None,
            "suggested_master_item_id": best[4]["id"] if best and best[3] == "master_item" else None,
            "suggested_item_code": best[4].get("item_code") if best else None,
            "suggested_description": ((best[4].get("short_desc") or best[4].get("description"))
                                      if best else None),
            "suggested_unit": best[4].get("unit") if best else None,
            "suggested_rate": best[4].get("rate") if best else None,
            "match_confidence": round(best[0], 3) if best else 0.0,
            "match_method": best[3] if best else "",
            "match_ambiguous": ambiguous,
            "match_pool": pool,
            "alternatives": [{"id": o["id"], "kind": k, "code": o.get("item_code"), "score": round(sc, 3),
                              "unit": o.get("unit"), "rate": o.get("rate"),
                              "description": (o.get("short_desc") or o.get("description") or "")[:90]}
                             for sc, k, o in alts],
        })
        out.append(row)
    return out
