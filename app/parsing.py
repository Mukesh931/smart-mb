"""
Smart-MB :: Estimate Parsing Engine  ("anchor-based extraction")

Handles the reality of PWD estimates: scanned PDFs, wrapped rows, columns that
drift page to page, OCR digit substitution (O/0, l/1, S/5).

Pipeline
  1. TEXT LAYER      : pypdf (layout mode) | openpyxl | csv | pasted text
  2. ANCHOR PASS     : find every Item-Code anchor  ^\\d+-\\d+-\\d+$  (OCR-normalised)
  3. QUANTITY PASS   : unit-token anchored -> header-column band -> row-end -> continuation
  4. MASTER LOOKUP   : anchor is authoritative; description/unit/rate come from the
                       admin-controlled Master CSR.  PDF text is used only as a
                       fallback for codes that are not in the Master DB (non-schedule).
  5. RECONCILIATION  : matched / quantity-mismatch / unit-mismatch / unknown-item
"""
from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Iterable

# ------------------------------------------------------------------ constants
CODE_RE = re.compile(r"(?<![0-9A-Za-z])([0-9OoIlSs]{1,2})-([0-9OoIlSs]{1,2})-([0-9OoIlSs]{1,2})(?![0-9A-Za-z])")
NUM_RE = re.compile(r"(?<![\dA-Za-z.])(\d{1,7}(?:\.\d{1,3})?)(?![\dA-Za-z])")
UNIT_TOKENS = {
    "m": "m", "mtr": "m", "mtrs": "m", "meter": "m", "metre": "m", "rm": "m",
    "each": "Each", "each.": "Each", "no": "Nos", "nos": "Nos", "no.": "Nos", "nos.": "Nos",
    "point": "Point", "points": "Point", "set": "Set", "kg": "kg", "kgs": "kg",
    "bag": "Bag", "lot": "Lot", "ls": "LS", "sqm": "sqm", "sq.m": "sqm", "cum": "cum",
    "per": None, "rate": None, "amount": None, "unit": None, "total": None, "page": None,
}
OCR_MAP = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "s": "5", "B": "8"})
SKIP_NUM_PREFIX = ("%",)

# OCR confusion sets used to repair item codes that miss the Master lookup.
# '1-0-1' (an O mis-read as zero) is a classic; we heal it by generating all
# one-character repairs and ranking them against the master descriptions.
CONFUSION = {
    "0": "OQD8619", "1": "Il741", "2": "Z7", "3": "8", "4": "1A9",
    "5": "S6", "6": "G5B80", "7": "12T", "8": "B3S6", "9": "gq47",
}
SPEC_FOLLOWERS = ("mm", "cm", "sq", "kg", "kw", "kv", "amp", "wo", "dia", "x", "×", "/", "w,", "w.", "%")
UNIT_WORD_STOP = {"point", "each", "nos", "no", "m", "mtr", "kg", "bag", "lot", "set"}

# --------------------------------------------------------------- AI prompt block
AI_PROMPT_TEMPLATE = """You are an Estimate Mapper.

Input 1: A raw text stream from a scanned PWD (Maharashtra) Electrical Estimate PDF.
Input 2: A list of valid Item Codes from our Master CSRD database, e.g. ['1-1-1', '1-1-2', ... '7-4-2'].

Task:
1. Identify every valid Item Code in the PDF text. Item codes follow the pattern
   <chapter>-<section>-<item>   e.g. 1-3-4, 2-1-11, 9-1-1.  Repair obvious OCR
   substitution errors (O->0, l/I->1, S->5) before validating against the master list.
2. Extract the 'Quantity' associated with that code. Quantities normally sit at the
   end of the row, immediately after the Unit column (m, Each, Point, kg, Nos).
   Ignore material rate / labour rate / amount columns.
3. Return STRICT JSON only, no prose:
   [{{"code": "1-1-2", "pdf_qty": 50, "unit": "m", "confidence": 0.0-1.0}}]
4. If a code is found in the PDF but NOT in the Master list, return it with
   "code": "<as printed>", "pdf_qty": <qty>, "status": "Unknown_Item" and include the
   surrounding raw line as "raw_text" so a human can map it manually.
5. Never invent quantities. If no quantity can be associated with a code, return
   "pdf_qty": null with your best guess of the reason in "note".

Master Item Codes available (truncated list): {codes}
"""


# ------------------------------------------------------------------ extraction
def extract_text_from_pdf(path: str) -> tuple[str, str, bool]:
    """Return (text, engine, needs_ocr)."""
    try:
        from pypdf import PdfReader
    except Exception:  # pragma: no cover
        return "", "none", True
    try:
        reader = PdfReader(path)
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text(extraction_mode="layout") or "")
            except Exception:
                chunks.append(page.extract_text() or "")
        text = "\n".join(chunks)
        needs_ocr = len(re.sub(r"\s", "", text)) < 120 * max(1, len(reader.pages))
        return text, "pypdf-layout", needs_ocr
    except Exception as exc:  # pragma: no cover
        return f"__PARSE_ERROR__ {exc}", "pypdf-layout", True


def extract_rows_from_xlsx(path: str) -> list[dict]:
    """Return list of raw rows (list-of-cells) from every sheet, flattened to text lines."""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    rows: list[dict] = []
    for ws in wb.worksheets:
        for r_i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = ["" if c is None else str(c).strip() for c in row]
            if not any(cells):
                continue
            rows.append({"sheet": ws.title, "row": r_i, "cells": cells})
    return rows


def rows_to_text(rows: Iterable[dict]) -> str:
    out = []
    for r in rows:
        out.append("  ".join(c for c in r["cells"] if c))
    return "\n".join(out)


def extract_text_from_csv(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        rows = list(csv.reader(fh, dialect))
    return "\n".join("  ".join(c for c in row if c) for row in rows if any(row))


def extract_any(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text, engine, needs_ocr = extract_text_from_pdf(path)
        return {"text": text, "engine": engine, "needs_ocr": needs_ocr, "file_type": "pdf"}
    if ext in (".xlsx", ".xlsm", ".xls"):
        rows = extract_rows_from_xlsx(path)
        return {"text": rows_to_text(rows), "engine": "openpyxl", "needs_ocr": False,
                "file_type": "excel", "rows": rows}
    if ext in (".csv", ".txt", ".tsv"):
        text = extract_text_from_csv(path) if ext != ".txt" else open(path, encoding="utf-8", errors="replace").read()
        return {"text": text, "engine": "csv" if ext != ".txt" else "plain-text",
                "needs_ocr": False, "file_type": ext.lstrip(".")}
    text = open(path, encoding="utf-8", errors="replace").read()
    return {"text": text, "engine": "plain-text", "needs_ocr": False, "file_type": "text"}


# ------------------------------------------------------------- code handling
def normalise_code(raw: str) -> str:
    return raw.translate(OCR_MAP)


def _code_like(part: str) -> bool:
    return bool(re.fullmatch(r"[0-9OoIlSs]{1,2}", part))


def find_anchors(text: str) -> list[dict]:
    anchors = []
    for m in CODE_RE.finditer(text):
        a, b, c = m.group(1), m.group(2), m.group(3)
        norm = f"{normalise_code(a)}-{normalise_code(b)}-{normalise_code(c)}"
        # drop impossible anchors (e.g. years like 2024-25) - chapter <= 20, section <= 30
        try:
            ch, sec, itm = (int(x) for x in norm.split("-"))
        except ValueError:
            continue
        if ch > 30 or sec > 40 or itm > 99:
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line_end = len(text) if line_end == -1 else line_end
        # Which characters were actually non-numeric when printed?  Only these positions
        # may be surgically repaired later - a clean code that simply isn't in the Master
        # CSR must stay flagged as Unknown_Item / non-schedule.
        ocr_positions = []
        for pi, part in enumerate((m.group(1), m.group(2), m.group(3))):
            for ci, ch in enumerate(part):
                if not ch.isdigit():
                    ocr_positions.append([pi, ci])
        anchors.append({
            "raw": m.group(0),
            "code": norm,
            "ocr_positions": ocr_positions,
            "ocr_repaired": bool(ocr_positions),
            "start": m.start(),
            "end": m.end(),
            "line_no": text.count("\n", 0, m.start()) + 1,
            "line": text[line_start:line_end].strip(),
        })
    return anchors


def _detect_qty_column(text: str) -> tuple[int, int] | None:
    """Look for a header row containing a Quantity column; return its char band."""
    for line in text.split("\n")[:80]:
        low = line.lower()
        if ("qty" in low or "quantity" in low) and ("item" in low or "description" in low or "unit" in low):
            for word in re.finditer(r"(quantity|qty)\.?\s*(\(.*?\))?", low):
                return (word.start(), min(len(line), word.end() + 60))
        if re.search(r"\b(quantity|qty)\b", low) and re.search(r"\bunit\b", low):
            idx = low.find("qty")
            return (idx, min(len(line), idx + 60))
    return None


def _numbers_in(segment: str, offset: int = 0) -> list[dict]:
    nums = []
    for m in NUM_RE.finditer(segment):
        token = m.group(1)
        if m.start() > 0 and segment[m.start() - 1] == "%":
            continue
        abs_start = offset + m.start()
        nums.append({"value": float(token), "start": abs_start, "end": offset + m.end(), "raw": token})
    return nums


def _unit_anchors(segment: str, abs_offset: int) -> list[dict]:
    """Find unit-column tokens.

    A unit token only counts when it behaves like a table cell - i.e. it is either
    directly followed by the quantity ('Point    84') or is the last word on the row.
    Plain English words that happen to match a unit ('light point wiring') are ignored,
    which is what broke naive matching on wrapped descriptions.
    """
    cands: list[dict] = []
    pattern = re.compile(r"(?<![A-Za-z0-9.])([A-Za-z.]{1,6})(?![A-Za-z0-9])")
    matches = list(pattern.finditer(segment))
    for idx, m in enumerate(matches):
        key = m.group(1).lower().strip(".")
        unit = UNIT_TOKENS.get(key)
        if not unit:
            continue
        # The gap between the unit column and the quantity column is *only* whitespace /
        # column separators - a big run of spaces is the signature of two table cells.
        tail = segment[m.end():m.end() + 150]
        num_match = re.match(r"[ \t|.:;)\-]{0,149}?(\d{1,7}(?:\.\d{1,3})?)(?![0-9.])", tail)
        trailing_word = re.match(r"\s*([A-Za-z]{2,})", tail)
        cands.append({
            "unit": unit, "pos": m.start(), "abs_end": abs_offset + m.end(),
            "qty": float(num_match.group(1)) if num_match else None,
            "is_last_token": idx == len(matches) - 1 or not re.search(r"[A-Za-z0-9]", tail[:12]),
            "followed_by_word": bool(trailing_word),
        })
    return cands


def _looks_like_spec(segment: str, num: dict) -> bool:
    """True when a number is a specification ('1.5 sq.mm', '20 mm dia', '1200 x 300') and
    therefore should not be mistaken for the quantity column."""
    after = segment[num["end"]:num["end"] + 6].strip().lower()
    return any(after.startswith(f) for f in SPEC_FOLLOWERS)


def extract_quantities(anchors: list[dict], text: str, qty_band: tuple[int, int] | None) -> list[dict]:
    """Attach the most probable quantity to each anchor using cascading strategies."""
    results: list[dict] = []
    for i, a in enumerate(anchors):
        seg_end = anchors[i + 1]["start"] if i + 1 < len(anchors) else min(len(text), a["end"] + 400)
        segment = text[a["end"]:seg_end]
        abs_offset = a["end"]
        nums = _numbers_in(segment, abs_offset)
        note, method, confidence, qty, unit_hint = "", "", 0.0, None, None

        unit_cands = _unit_anchors(segment, abs_offset)

        # 1. strong: unit token immediately followed by a number (the table-cell signature)
        strong = [c for c in unit_cands if c["qty"] is not None]
        if strong:
            pick = strong[-1]  # last one on the row = the real unit/quantity column
            qty, method, confidence, unit_hint = pick["qty"], "unit-anchored", 0.96, pick["unit"]
            if len(strong) > 1 and min(c["pos"] for c in strong) != pick["pos"]:
                confidence = 0.9
                note = "multiple unit-like tokens on the row - the last unit/quantity pair was used"

        # 2. weak: unit word concluding the row, quantity printed on the wrapped line below
        if qty is None:
            trailing = [c for c in unit_cands if c["is_last_token"] and not c["followed_by_word"]]
            if trailing:
                tail_text = segment[trailing[-1]["pos"]:]
                cnums = _numbers_in(tail_text, abs_offset + trailing[-1]["pos"])
                if cnums:
                    qty, method, confidence, unit_hint = cnums[0]["value"], "unit-wrapped", 0.66, trailing[-1]["unit"]
                    note = "unit and quantity split across a wrapped row"

        # 3. header column band (when the document has a proper Quantity column header)
        if qty is None and qty_band:
            band_nums = [n for n in nums if qty_band[0] - 12 <= (n["start"] - abs_offset) <= qty_band[1]]
            if band_nums:
                qty, method, confidence = band_nums[0]["value"], "header-column", 0.9

        # 4. row-end numeric: abstracts put the quantity last on the row
        if qty is None and nums:
            pool = []
            for n in nums:
                rel = {"start": n["start"] - abs_offset, "end": n["end"] - abs_offset}
                if rel["start"] >= 0 and not _looks_like_spec(segment, rel):
                    pool.append(n)
            pool = pool or nums
            last = (pool or nums)[-1]
            qty, method, confidence = last["value"], "row-end-numeric", 0.84
            if len(nums) >= 3:
                confidence, note = 0.66, ("row carries several numeric columns - quantity may in fact be the "
                                          "rate or amount column; verify at site")

        # 5. continuation line
        if qty is None:
            cont = text[seg_end: seg_end + 260]
            cnums = _numbers_in(cont, seg_end)
            if cnums:
                qty, method, confidence = cnums[0]["value"], "continuation-line", 0.55
                note = "quantity recovered from the following line (wrapped row)"

        if qty is None:
            method, confidence, note = "not-found", 0.0, "no numeric quantity near this item code"

        results.append({
            "item_code": a["code"],
            "printed_code": a["raw"],
            "ocr_positions": a.get("ocr_positions", []),
            "ocr_repaired": a["ocr_repaired"],
            "pdf_qty": qty,
            "unit_hint": unit_hint,
            "confidence": round(confidence, 3),
            "method": method,
            "line_no": a["line_no"],
            "raw_line": a["line"][:400],
            "note": note,
        })
    return results


def repair_code(code: str, master_index: dict[str, dict], context_text: str = "",
                ocr_positions: list | None = None) -> tuple[str, float, dict | None]:
    """Heal OCR-mangled item codes against the Master CSR.

    '1-O-1' normalises to '1-0-1', which does not exist -> we generate every plausible
    one-character repair (0<->O/D/Q/8, 1<->I/l/7, 5<->S, 8<->B, ...) and rank the
    survivors by how well their master description matches the wording printed in the
    estimate row.  This is how a blurry scan still lands on the exact legal description.
    """
    if code in master_index:
        return code, 1.0, None
    if not ocr_positions:
        # Nothing in the printed code looks like an OCR artefact -> this is a genuine
        # non-schedule / out-of-schedule item.  Do NOT guess (the spec requires it to be
        # flagged as Unknown_Item so a human maps it).
        return code, 0.0, None
    parts = code.split("-")
    candidates: list[str] = []
    for pi, part in enumerate(parts):
        for ci, ch in enumerate(part):
            if [pi, ci] not in [list(x) for x in ocr_positions]:
                continue      # position was printed cleanly - repairing it would be a guess
            for alt in CONFUSION.get(ch, ""):
                if not alt.isdigit():
                    continue
                new = part[:ci] + alt + part[ci + 1:]
                cand = "-".join(parts[:pi] + [new] + parts[pi + 1:])
                if cand in master_index and cand != code:
                    candidates.append(cand)
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return code, 0.0, None
    ctx = set(re.findall(r"[a-z]{3,}", (context_text or "").lower()))
    best, best_score, alternatives = None, -1.0, []
    for cand in candidates:
        toks = set(re.findall(r"[a-z]{3,}", master_index[cand]["description"].lower()))
        inter, union = len(ctx & toks), len(ctx | toks) or 1
        score = inter / union
        alternatives.append({"code": cand, "similarity": round(score, 3)})
        if score > best_score:
            best, best_score = cand, score
    if best_score < 0.12:
        # No candidate even remotely resembles the printed wording - leave it for manual mapping.
        return code, 0.0, {"printed": code, "rejected_candidates":
                           sorted(alternatives, key=lambda x: -x["similarity"])[:3]}
    confidence = round(min(0.94, 0.6 + 0.34 * max(best_score, 0.0)), 3)
    meta = {"printed": code, "repaired_to": best, "similarity": round(best_score, 3),
            "alternatives": sorted(alternatives, key=lambda x: -x["similarity"])[:4]}
    return best, confidence, meta


# ------------------------------------------------------------------- mapping
def map_to_master(parsed: list[dict], master_index: dict[str, dict]) -> dict:
    items, unknown = [], []
    for row in parsed:
        m = master_index.get(row["item_code"])
        repair_meta = None
        if not m:
            healed, conf, repair_meta = repair_code(row["item_code"], master_index,
                                                    f"{row.get('raw_line','')} {row.get('note','')}",
                                                    row.get("ocr_positions"))
            if repair_meta:
                row = dict(row)
                row["item_code"] = healed
                row["ocr_repaired"] = True
                row["confidence"] = min(row["confidence"] or 0.5, conf)
                m = master_index[healed]
        if not m:
            row = dict(row)
            row["status"] = "unknown_item"
            row["master"] = None
            row["flags"] = ["Item code not present in Master CSR for this FY/region - manual mapping required"]
            unknown.append(row)
            continue
        flags = []
        if row["unit_hint"] and row["unit_hint"] != m["unit"]:
            flags.append(f"Unit in PDF ({row['unit_hint']}) differs from Master ({m['unit']}) - Master unit applied")
        if row["confidence"] < 0.7 and row["pdf_qty"] is not None:
            flags.append("Low confidence quantity extraction - verify on site")
        if row["pdf_qty"] is None:
            flags.append("Quantity could not be read - enter manually")
        if row["ocr_repaired"]:
            if repair_meta:
                flags.append(f"OCR repair: printed '{repair_meta['printed']}' -> mapped to master item "
                             f"'{repair_meta['repaired_to']}' (specification similarity {repair_meta['similarity']:.0%})")
            else:
                flags.append(f"OCR artefact repaired: '{row['printed_code']}' -> '{row['item_code']}'")
        row = dict(row)
        row["status"] = "matched"
        row["master"] = m
        row["flags"] = flags
        row["repair"] = repair_meta
        items.append(row)
    return {"items": items, "unknown": unknown}


def parse_estimate_text(text: str, master_index: dict[str, dict], *, merge: str = "max") -> dict:
    """Master parse entry point.  merge: max | sum | first  (duplicate anchors)."""
    anchors = find_anchors(text)
    qty_band = _detect_qty_column(text)
    parsed = extract_quantities(anchors, text, qty_band)

    # merge duplicate anchors (rate-analysis notes, page headers, TOC)
    merged: dict[str, dict] = {}
    duplicates: list[dict] = []
    for row in parsed:
        code = row["item_code"]
        if code not in merged:
            merged[code] = row
            continue
        prev = merged[code]
        vals = [v for v in (prev["pdf_qty"], row["pdf_qty"]) if v is not None]
        duplicates.append({"item_code": code, "line_no": row["line_no"], "pdf_qty": row["pdf_qty"],
                           "kept": prev["pdf_qty"]})
        if not vals:
            continue
        if merge == "sum":
            new_val = round(sum(vals), 3)
        elif merge == "first":
            new_val = vals[0]
        else:
            new_val = max(vals)
        if prev["pdf_qty"] is None or new_val != prev["pdf_qty"]:
            prev["note"] = (prev["note"] + " | " if prev["note"] else "") + \
                f"duplicate anchor merged ({merge}) from line {row['line_no']}"
            prev["pdf_qty"] = new_val
            prev["confidence"] = min(prev["confidence"], 0.7)

    mapped = map_to_master(list(merged.values()), master_index)
    est_amount = sum((r["master"]["rate"] or 0) * (r["pdf_qty"] or 0) for r in mapped["items"])
    return {
        "engine": "anchor-regex-v1",
        "items": mapped["items"],
        "unknown": mapped["unknown"],
        "duplicates": duplicates,
        "stats": {
            "anchors_found": len(anchors),
            "unique_codes": len(merged),
            "matched": len(mapped["items"]),
            "unknown": len(mapped["unknown"]),
            "low_confidence": sum(1 for r in mapped["items"] if r["confidence"] < 0.7),
            "qty_column_band": qty_band,
            "estimated_amount": round(est_amount, 2),
            "duplicates": len(duplicates),
        },
        "ai_prompt": AI_PROMPT_TEMPLATE.format(codes=str(list(master_index.keys())[:120])),
    }


def build_master_index(rows: Iterable[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for r in rows:
        idx[r["item_code"]] = {
            "id": r["id"], "item_code": r["item_code"], "description": r["description"],
            "short_desc": r.get("short_desc"), "unit": r["unit"], "rate": r["rate"],
            "category": r.get("category"), "section": r.get("section"), "spec_no": r.get("spec_no"),
            "chapter": r.get("chapter"),
            "tags": _tags(r.get("tags")),
        }
    return idx


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    import json
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return [t.strip() for t in str(value).split(",") if t.strip()]


# ----------------------------------------------------- CSV/Excel CSR importer
CSR_COLUMN_ALIASES = {
    "item_code": ["item no", "itemno", "item code", "code", "item", "item no.", "sr no", "sr. no", "sr.no"],
    "description": ["description", "short description", "desc", "particulars", "item description"],
    "unit": ["unit", "uom", "units"],
    "rate": ["rate", "completed rate", "total rate", "total", "rate rs", "rate (rs)", "amount"],
    "material_rate": ["material", "material rate", "supply rate", "supply"],
    "labour_rate": ["labour", "labor", "labour rate", "erection", "erection rate"],
    "category": ["category", "head", "group"],
    "section": ["section", "sub head", "subhead", "specification"],
    "spec_no": ["spec", "spec no", "specification no", "spec no."],
    "tags": ["tags", "tag", "metadata"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 .]", "", str(s or "").strip().lower())


FIELD_PRIORITY = ["item_code", "description", "unit", "material_rate", "labour_rate", "rate",
                  "category", "section", "spec_no", "tags"]
FUZZY_FIELDS = ["description", "section", "spec_no", "tags", "category"]


def detect_columns(header_cells: list[str]) -> dict[str, int]:
    """Map spreadsheet headers to fields.

    Two passes: exact alias matches first (most specific fields first, so a
    'Material Rate' column is never mistaken for the completed 'Rate'), then fuzzy
    substring matching only for descriptive fields.
    """
    mapping: dict[str, int] = {}
    norm = [_norm(c) for c in header_cells]
    for field in FIELD_PRIORITY:
        for idx, n in enumerate(norm):
            if not n or idx in mapping.values():
                continue
            if n in CSR_COLUMN_ALIASES[field]:
                mapping[field] = idx
                break
    for field in FUZZY_FIELDS:
        if field in mapping:
            continue
        for idx, n in enumerate(norm):
            if not n or idx in mapping.values():
                continue
            if any(a in n for a in CSR_COLUMN_ALIASES[field] if len(a) >= 6):
                mapping[field] = idx
                break
    return mapping


def parse_csr_rows(rows: list[list[Any]]) -> dict:
    """Rows = list of raw cell lists (first non-empty row treated as header if it maps)."""
    headers_idx, columns = None, {}
    for i, row in enumerate(rows[:15]):
        cells = [str(c) if c is not None else "" for c in row]
        cols = detect_columns(cells)
        if "item_code" in cols and ("description" in cols or "rate" in cols):
            headers_idx, columns, header_cells = i, cols, cells
            break
    if headers_idx is None:
        return {"ok": False, "error": "Could not detect a header row with Item Code + (Description|Rate).",
                "preview": rows[:8], "columns": {}}

    out, errors = [], []
    for r_i, row in enumerate(rows[headers_idx + 1:], start=headers_idx + 2):
        cells = [str(c) if c is not None else "" for c in row]
        if not any(c.strip() for c in cells):
            continue
        code_raw = cells[columns["item_code"]] if columns.get("item_code") is not None and columns["item_code"] < len(cells) else ""
        code = normalise_code(re.sub(r"[^0-9OoIlSs-]", "", code_raw.strip()))
        if not re.fullmatch(r"\d{1,2}-\d{1,2}-\d{1,2}", code):
            if code_raw.strip():
                errors.append({"row": r_i, "reason": f"Item code '{code_raw}' is not in <ch>-<sec>-<item> format"})
            continue

        def cell(field: str) -> str:
            i = columns.get(field, -1)
            return cells[i].strip() if 0 <= i < len(cells) else ""

        def num(field: str) -> float:
            raw = cell(field).replace(",", "").replace("₹", "")
            m = re.search(r"-?\d+(?:\.\d+)?", raw)
            return float(m.group(0)) if m else 0.0

        desc = cell("description") or cell("section")
        if not desc:
            errors.append({"row": r_i, "reason": "Missing description"})
            continue
        rate = num("rate")
        if rate == 0:
            rate = num("material_rate") + num("labour_rate")
        if rate == 0:
            errors.append({"row": r_i, "reason": f"Rate missing/zero for {code}"})
            continue
        out.append({
            "item_code": code,
            "description": desc,
            "unit": cell("unit") or "Each",
            "rate": rate,
            "material_rate": num("material_rate"),
            "labour_rate": num("labour_rate"),
            "category": cell("category"),
            "section": cell("section"),
            "spec_no": cell("spec_no"),
            "tags": [t.strip() for t in re.split(r"[,;|]", cell("tags")) if t.strip()],
            "chapter": int(code.split("-")[0]),
        })
    return {"ok": True, "columns": columns, "header_row": headers_idx + 1, "items": out,
            "errors": errors[:60], "error_count": len(errors)}
