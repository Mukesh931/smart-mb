"""
Smart-MB :: end-to-end smoke test.

Drives the full platform against a running server:
  login -> create project -> parse estimate PDF -> commit import -> checklist
  -> extra item from Master CSR -> measurements + photo -> deviations
  -> Form-23 PDF/Excel -> admin CSR import (preview + commit) -> audit trail

Usage:  python3 -m tools.smoke_test [base_url]
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = {"v": ""}
PASS, FAIL = [], []


def call(path, method="GET", body=None, raw=False, token=None, expect=None):
    req = urllib.request.Request(BASE + path, method=method)
    tok = token if token is not None else TOKEN["v"]
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data) as res:
            payload = res.read()
            return res.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload or b"{}")
        except Exception:
            return e.code, {"detail": payload[:200].decode(errors="replace")}


def upload(path, filepath, fields=None, token=None, expect=None):
    boundary = "----smartmb" + uuid.uuid4().hex
    parts = []
    for k, v in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    with open(filepath, "rb") as fh:
        content = fh.read()
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(filepath)}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    data = b"".join(parts)
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    tok = token if token is not None else TOKEN["v"]
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read()[:300].decode(errors="replace")}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


print(f"Smart-MB smoke test -> {BASE}\n")
print("[1] auth")
st, r = call("/api/health")
check("health endpoint", st == 200 and r.get("ok"), f"{r.get('items')} master items")
st, r = call("/api/auth/login", "POST", {"email": "je.nashik@pwd.maharashtra.gov.in", "password": "Engineer@123"})
check("engineer login", st == 200 and r.get("token"), r.get("user", {}).get("division", ""))
ETOK = r.get("token", "")
st, r = call("/api/auth/login", "POST", {"email": "admin@pwd.maharashtra.gov.in", "password": "Admin@123"})
check("admin login", st == 200 and r.get("token"))
ATOK = r.get("token", "")
st, r = call("/api/auth/login", "POST", {"email": "admin@pwd.maharashtra.gov.in", "password": "wrong"})
check("bad password rejected", st == 401)
st, r = call("/api/csr/versions", token=ETOK)
check("engineer cannot reseed master", call("/api/admin/reseed", "POST", token=ETOK)[0] == 403)
check("CSR versions listed", st == 200 and len(r["versions"]) == 14, f"{len(r.get('versions', []))} versions")

print("\n[2] master CSR browse & search")
st, r = call("/api/csr/items?fy=2024-25&region=Nashik&limit=5&q=earthing", token=ETOK)
check("csr search 'earthing'", st == 200 and r["total"] > 3, f"{r.get('total')} hits")
st, r = call("/api/csr/suggest?q=exhaust+fan&fy=2024-25&region=Nashik", token=ETOK)
fan = r["results"][0] if r.get("results") else {}
check("'Exhaust Fan' resolves to a master item", fan.get("item_code", "").startswith("3-1-"), f"{fan.get('item_code')} @ Rs {fan.get('rate')}")
st, r = call("/api/csr/suggest?q=chemical+earthing&fy=2024-25&region=Nashik", token=ETOK)
check("'chemical earthing' tagged item found", bool(r.get("results")) and r["results"][0]["item_code"] == "9-1-4",
      r["results"][0]["item_code"] if r.get("results") else "")

print("\n[3] Flow B - create project")
st, r = call("/api/projects", "POST", {
    "name": "FAT Test - Electrical installation to Primary Health Centre, Dindori",
    "scheme": "Test scheme", "division": "PWD Electrical Sub-Division, Nashik", "circle": "Nashik Circle",
    "region": "Nashik", "csr_fy": "2024-25", "csr_region": "Nashik", "estimate_no": "EST/TEST/001",
    "ts_no": "TS/TEST/001", "ts_date": "2025-02-01", "mb_no": "MB-TEST", "agreement_no": "AG/TEST/001",
    "agency": "M/s Test Electricals",
    "rooms": [{"floor": "Ground Floor", "name": "OPD Room"}, {"floor": "Ground Floor", "name": "Store"},
              {"floor": "First Floor", "name": "Ward A"}],
}, token=ETOK)
check("project created", st == 200 and r.get("project_id"), r.get("project_code", ""))
PID = r.get("project_id")

print("\n[4] Flow B - parse the estimate (messy PDF)")
st, parsed = upload(f"/api/projects/{PID}/parse-estimate", os.path.join(ROOT, "samples", "sample_estimate.pdf"),
                    {"engine": "anchor"}, token=ETOK)
stt = parsed.get("stats", {})
check("parse returns rows", st == 200 and len(parsed.get("rows", [])) > 10, f"{len(parsed.get('rows', []))} rows")
check("anchors found", stt.get("anchors_found", 0) >= 19, str(stt.get("anchors_found")))
check("matched against master", stt.get("matched", 0) == 18, str(stt.get("matched")))
check("non-schedule flagged as Unknown_Item", stt.get("unknown") == 1,
      (parsed.get("rows", [{}])[-1] or {}).get("item_code", ""))
check("OCR artefact cured (1-O-1 -> 1-1-1)",
      any(r["item_code"] == "1-1-1" and r["printed_code"] != "1-1-1" for r in parsed["rows"]))
check("descriptions come from the Master CSR",
      all(len(r.get("description", "")) > 40 for r in parsed["rows"] if r["status"] == "matched"))
check("duplicate anchor merged", stt.get("duplicates", 0) >= 1)

print("\n[4b] Flow B2 - printed-table abstract (Work Abstract layout, code in brackets)")
st, abs_parsed = upload(f"/api/projects/{PID}/parse-estimate",
                        os.path.join(ROOT, "samples", "sample_work_abstract.pdf"),
                        {"engine": "auto"}, token=ETOK)
astats = abs_parsed.get("stats", {})
check("column engine chosen for a printed table", abs_parsed.get("engine") == "pdf-column-table",
      str(abs_parsed.get("engine")))
check("every row read from its own column", astats.get("rows_read", 0) == 9, str(astats.get("rows_read")))
ab_rows = {r["item_code"]: r for r in abs_parsed.get("rows", [])}
check("code read from the bracketed anchor", "1-3-14" in ab_rows and "16-3-9" in ab_rows,
      ", ".join(sorted(ab_rows))[:60])
check("quantities taken from the Quantity column, not the rate/amount",
      ab_rows.get("9-1-4", {}).get("tendered_qty") == 12 and ab_rows.get("1-3-14", {}).get("tendered_qty") == 55,
      f"9-1-4={ab_rows.get('9-1-4', {}).get('tendered_qty')} 1-3-14={ab_rows.get('1-3-14', {}).get('tendered_qty')}")
check("every amount cross-checked against qty x rate", astats.get("amount_cross_checked", 0) == 9,
      str(astats.get("amount_cross_checked")))
check("non-schedule row keeps the abstract's own rate and unit",
      ab_rows.get("1-3-14", {}).get("rate") == 61 and ab_rows.get("1-3-14", {}).get("unit") == "m",
      f"{ab_rows.get('1-3-14', {}).get('rate')} / {ab_rows.get('1-3-14', {}).get('unit')}")
check("item absent from this version is matched from another CSR year when possible",
      ab_rows.get("9-1-4", {}).get("status") in ("matched", "matched_other_version"),
      str(ab_rows.get("9-1-4", {}).get("status")))
check("the engineer is told why the rest did not match", bool(abs_parsed.get("hint")),
      (abs_parsed.get("hint") or "")[:70])

print("\n[5] Flow B - commit the reconciliation & build the checklist")
rows = parsed["rows"]
for r in rows:
    if r["is_non_schedule"]:
        r["description"] = "Providing and fixing 40 W LED street light luminaire (non-schedule, market rate)"
        r["rate"] = 6800
st, r = call(f"/api/projects/{PID}/import-estimate", "POST",
             {"rows": rows, "create_rooms_from_text": True}, token=ETOK)
check("import committed", st == 200 and len(r.get("created", [])) == len(rows), f"created {len(r.get('created', []))}")
st, cl = call(f"/api/projects/{PID}/checklist", token=ETOK)
check("checklist generated", st == 200 and cl["totals"]["items"] >= 19, f"{cl['totals']['items']} items")
check("auto rooms detected from estimate text", len(cl["rooms"]) >= 3, f"{len(cl['rooms'])} rooms")

print("\n[6] Flow C - onsite measurement")
item = cl["items"][0]
st, r = call("/api/measurements", "POST", {
    "project_item_id": item["id"], "room_id": cl["rooms"][0]["id"], "nos": 1, "length": 62.5,
    "notes": "Measured jointly with contractor representative.", "measured_on": "2025-03-04"}, token=ETOK)
check("measurement recorded", st == 200 and r["measured_qty"] == 62.5, f"qty {r.get('measured_qty')}")
MID = r.get("measurement_id")
st, r = call("/api/measurements", "POST", {
    "project_item_id": item["id"], "room_id": cl["rooms"][1]["id"], "nos": 1, "length": 24.5,
    "measured_on": "2025-03-05"}, token=ETOK)
check("second run sums to the item total", r.get("item_total_qty") == 87.0,
      f"total {r.get('item_total_qty')} vs tendered {r.get('tendered_qty')}")
st, r = upload(f"/api/measurements/{MID}/photos", os.path.join(ROOT, "samples", "MasterCSR_import_template.xlsx"),
               {"caption": "site"}, token=ETOK)
check("non-image photo rejected", st == 400)

print("\n[7] Flow C - extra item straight from the Master CSR")
st, r = call(f"/api/projects/{PID}/items", "POST",
             {"master_item_id": fan["id"], "tendered_qty": 4, "source": "extra"}, token=ETOK)
check("extra item added with CSR code + rate", st == 200 and r["item_code"] == fan["item_code"] and r["rate"] > 0,
      f"{r.get('item_code')} Rs {r.get('rate')}/{r.get('unit')}")
st, r = call(f"/api/projects/{PID}/items", "POST",
             {"description": "Special non-schedule item", "unit": "Each", "rate": 1500, "tendered_qty": 2,
              "is_non_schedule": True, "ns_reason": "Not in CSR - approved vide letter", "source": "manual"}, token=ETOK)
check("non-schedule manual item", st == 200 and r["non_schedule"] is True)
st, r = call(f"/api/projects/{PID}/items", "POST", {"master_item_id": fan["id"], "tendered_qty": 1}, token=ETOK)
check("duplicate item rejected", st == 409)

print("\n[8] Flow 6 - deviation alerts")
st, cl2 = call(f"/api/projects/{PID}/checklist", token=ETOK)
st, dev = call(f"/api/projects/{PID}/deviations", token=ETOK)
check("deviation statement covers every checklist item", st == 200 and len(dev["rows"]) == cl2["totals"]["items"],
      f"{len(dev.get('rows', []))} rows vs {cl2['totals']['items']} checklist items")
check("excess/saving valued at CSR rates", "excess_amount" in dev["totals"] and "saving_amount" in dev["totals"],
      f"net {dev['totals']['net_amount']}")
row = [x for x in dev["rows"] if x["measured"] > 0][0]
check("deviation math per item", abs(row["dev_amount"] - row["dev_qty"] * row["rate"]) < 0.01,
      f"{row['item_code']}: {row['measured']} vs {row['tendered']}")

st, _items = call(f"/api/projects/{PID}/items", token=ETOK)
_mi = _items["items"][0]["id"]

print("\n[8b] Descriptive schedule - extract, reconcile, verify room-wise")
st, sch_prev = upload(f"/api/projects/{PID}/parse-schedule",
                      os.path.join(ROOT, "samples", "descriptive_schedule_sample.pdf"), token=ETOK)
check("descriptive schedule parsed", st == 200 and sch_prev.get("ok"), sch_prev.get("engine", ""))
sst = sch_prev.get("stats", {})
check("rotated column headers recovered", sst.get("columns") == 55, f"{sst.get('columns')} columns")
check("locations read as rooms (incl. the second TOILET)",
      sst.get("locations") == 14, f"{sst.get('locations')} locations")
check("every column reconciles with the printed GRAND TOTAL",
      sst.get("columns_reconciled") == sst.get("columns_checked") == 55,
      f"{sst.get('columns_reconciled')}/{sst.get('columns_checked')}")
_auto = [c for c in sch_prev["columns"]
         if (c.get("suggested_project_item_id") or c.get("suggested_master_item_id"))
         and not c.get("match_ambiguous") and (c.get("match_confidence") or 0) >= 0.5]
check("columns auto-mapped to the estimate / Master CSR", len(_auto) >= 5,
      f"{len(_auto)} auto-linked, {sum(1 for c in sch_prev['columns'] if c.get('match_ambiguous'))} flagged for confirmation")
check("no page/text warnings", not sch_prev.get("warnings"), "; ".join(sch_prev.get("warnings", []))[:80])

st, sch_imp = call(f"/api/projects/{PID}/import-schedule", "POST",
                   {"filename": "sample-schedule.pdf", "parsed": sch_prev}, token=ETOK)
check("schedule imported", st == 200 and sch_imp.get("doc_id"), json.dumps(sch_imp)[:90])
check("all 14 locations became project rooms", sch_imp.get("locations") == 14)
DOC = sch_imp.get("doc_id")

st, vwl = call(f"/api/projects/{PID}/verify", token=ETOK)
vt = vwl.get("totals", {})
check("room-wise worklist built", st == 200 and len(vwl.get("rooms", [])) == 14, f"{vt.get('cells')} quantities")
check("nothing verified yet", vt.get("pending") == vt.get("cells") and vt.get("kept") == 0)

# print reconciliation = estimate vs schedule
st, rec = call(f"/api/projects/{PID}/reconciliation", token=ETOK)
check("reconciliation sheet built", st == 200 and rec["totals"]["scheduled_items"] > 0,
      f"{rec['totals']['scheduled_items']} scheduled items")
check("unlinked columns surfaced for manual mapping", rec["totals"]["unmapped_columns"] >= 1,
      f"{rec['totals']['unmapped_columns']} columns")

# link one unmapped column to a Master CSR item, then verify it
orphan = rec["orphans"][0]
st, sug = call(f"/api/csr/suggest?q={urllib.parse.quote(orphan['col_label'])}&fy=2024-25&region=Nashik", token=ETOK)
cand = (sug.get("results") or [{}])[0]
st, mp = call(f"/api/schedule-docs/{DOC}/columns/{orphan['column_order']}/map", "POST",
              {"master_item_id": cand.get("id")}, token=ETOK)
check("unlinked column mapped to Master CSR", st == 200 and mp.get("item_code"), f"{orphan['col_label']} -> {mp.get('item_code')}")

st, vwl = call(f"/api/projects/{PID}/verify", token=ETOK)
cell = next(c for rm in vwl["rooms"] for c in rm["items"] if c["project_item_id"])
st, kept = call(f"/api/schedule-cells/{cell['id']}/verify", "POST", {"action": "keep"}, token=ETOK)
check("KEEP writes a measurement at the schedule quantity",
      st == 200 and kept["status"] == "kept" and kept["measurement_id"] and kept["qty"] == cell["qty"],
      f"sched {cell['qty']} -> {kept.get('qty')}")

other = next(c for rm in vwl["rooms"] for c in rm["items"]
             if c["project_item_id"] and c["id"] != cell["id"] and c["qty"] > 1)
st, changed = call(f"/api/schedule-cells/{other['id']}/verify", "POST",
                   {"action": "change", "actual_qty": 1, "note": "site count 1"}, token=ETOK)
check("CHANGE records the actual quantity", st == 200 and changed["status"] == "changed" and changed["qty"] == 1,
      f"schedule {other['qty']} -> actual 1")
st, same = call(f"/api/schedule-cells/{cell['id']}/verify", "POST",
                {"action": "change", "actual_qty": cell["qty"]}, token=ETOK)
check("an entered actual equal to the schedule is treated as KEEP", same.get("status") == "kept")
st, undo = call(f"/api/schedule-cells/{other['id']}/verify", "POST", {"action": "pending"}, token=ETOK)
check("verification can be reset", undo.get("status") == "pending")
st, redo = call(f"/api/schedule-cells/{other['id']}/verify", "POST",
                {"action": "change", "actual_qty": 2}, token=ETOK)

room = vwl["rooms"][0]
st, bulk = call(f"/api/schedule-docs/{DOC}/verify-bulk", "POST",
                {"action": "keep", "location_ids": [room["location_id"]]}, token=ETOK)
check("one-tap 'all as per schedule' for a room", st == 200 and bulk.get("verified", 0) >= 1,
      f"{bulk.get('verified')} quantities kept")

st, vwl2 = call(f"/api/projects/{PID}/verify", token=ETOK)
check("worklist progress advances", vwl2["totals"]["pending"] < vwl2["totals"]["cells"],
      f"{vwl2['totals']['progress_pct']}% verified")
st, meas = call(f"/api/projects/{PID}/measurements", token=ETOK)
marked = [m for m in meas["measurements"] if (m.get("notes") or "").startswith("[Schedule")]
check("schedule verification wrote real measurements", len(marked) >= 3, f"{len(marked)} rows")
st, xl, _ = call(f"/api/projects/{PID}/reconciliation.xlsx", raw=True, token=ETOK), None, None
check("control sheet exports to Excel", st[0] == 200 and len(st[1]) > 4000, f"{len(st[1])} bytes")

# offline safety: a reading queued on the phone is replayed with its client_ref
st, m1 = call("/api/measurements", "POST", {"project_item_id": _mi, "measured_qty": 3,
                                            "client_ref": "smoke-offline-1", "notes": "queued at site"}, token=ETOK)
st2, m2 = call("/api/measurements", "POST", {"project_item_id": _mi, "measured_qty": 3,
                                             "client_ref": "smoke-offline-1", "notes": "queued at site"}, token=ETOK)
check("replaying a queued reading does not double-count it",
      st == 200 and m2.get("duplicate") is True and m1["measurement_id"] == m2["measurement_id"],
      f"measurement {m1.get('measurement_id')} recognised on replay")
st, rows = call(f"/api/projects/{PID}/measurements", token=ETOK)
check("only one row exists for the replayed reading",
      len([r for r in rows["measurements"] if r.get("notes") == "queued at site"]) == 1)

st, sch_txt = call(f"/api/projects/{PID}/parse-schedule-text", "POST",
                   {"text": "Location\tItem\tUnit\tQty\nHALL\tLED panel 18W\tNos\t27\nTOILET\tEx. Fan\tNos\t1"}, token=ETOK)
check("paste mode reads a schedule table", st == 200 and sch_txt.get("ok"), sch_txt.get("engine", ""))

print("\n[9] Flow 6 - Form-23 MB generation")
req = urllib.request.Request(f"{BASE}/api/projects/{PID}/form23.pdf")
req.add_header("Authorization", "Bearer " + ETOK)
pdf = urllib.request.urlopen(req).read()
check("Form-23 PDF generated", pdf[:5] == b"%PDF-" and len(pdf) > 20000, f"{len(pdf) / 1024:.1f} KiB")
req = urllib.request.Request(f"{BASE}/api/projects/{PID}/form23.xlsx")
req.add_header("Authorization", "Bearer " + ETOK)
xls = urllib.request.urlopen(req).read()
check("Form-23 Excel generated (zip/xlsx)", xls[:2] == b"PK" and len(xls) > 5000, f"{len(xls) / 1024:.1f} KiB")
from pypdf import PdfReader  # noqa: E402  (only needed for this assertion)
reader = PdfReader(io.BytesIO(pdf))
pdf_text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
check("MB keeps the legal Master CSR wording", "as per specification" in pdf_text.lower()
      and "FORM No. 23" in pdf_text, f"{len(reader.pages)} page(s)")
check("MB carries the deviation statement + signature blocks",
      "DEVIATION STATEMENT" in pdf_text.upper() and "Executive Engineer" in pdf_text)
check("MB grand total row prints", "GRAND TOTAL" in pdf_text.upper() and "Total for Item" in pdf_text)
check("MB pages are numbered", "Page 1" in pdf_text)

print("\n[10] Admin - Master CSR import")
st, prev = upload("/api/csr/import", os.path.join(ROOT, "samples", "MasterCSR_import_template.xlsx"),
                  {"fy": "2025-26", "region": "Nashik", "mode": "merge", "commit": "false"}, token=ATOK)
check("import preview validates columns", st == 200 and prev.get("ok") and prev["item_count"] >= 2,
      f"detected {list(prev.get('columns_detected', {}).keys())}")
check("preview does not write", prev.get("committed") is False)
st, com = upload("/api/csr/import", os.path.join(ROOT, "samples", "MasterCSR_import_template.xlsx"),
                 {"fy": "2025-26", "region": "Nashik", "mode": "merge", "commit": "true"}, token=ATOK)
check("import commits to master DB", com.get("committed") and com.get("live_count", 0) >= 2,
      f"{com.get('live_count')} live items in 2025-26/Nashik")
st, r = call("/api/csr/items?fy=2025-26&region=Nashik&q=1-1-1", token=ATOK)
check("new FY version is queryable", r["total"] == 1 and abs(r["items"][0]["rate"] - 96) < 1,
      f"rate {r['items'][0]['rate'] if r.get('items') else '?'}")

print("\n[11] Admin - governance")
st, o = call("/api/admin/overview", token=ATOK)
check("admin overview", st == 200 and o["stats"]["master_rows"] > 2000, f"{o['stats']['master_rows']} rows")
check("audit trail records the actions",
      any(a["action"] == "ESTIMATE_IMPORTED" for a in o["audit"]) and
      any(a["action"] == "CSR_IMPORTED" for a in o["audit"]))
st, r = call("/api/admin/users", "POST", {"name": "Er. Test JE", "email": f"test.je.{uuid.uuid4().hex[:6]}@pwd.maharashtra.gov.in",
                                          "role": "engineer", "region": "Nashik"}, token=ATOK)
check("admin can create a user", st == 200 and r.get("user_id"))
NEW_UID = r.get("user_id")
st, me = call("/api/auth/me", token=ATOK)
ATOK_UID = me["user"]["id"]
st, r = call("/api/admin/users", "POST", {"name": "x", "email": o["users"][0]["email"]}, token=ATOK)
check("duplicate email rejected", st == 409)
st, r = call("/api/projects", "POST", {"name": "no auth project"}, token="")
check("unauthenticated writes rejected", st in (401, 403))

print("\n[12] cleanup - remove every artifact this test created")
st, r = call(f"/api/projects/{PID}", "DELETE", token=ATOK)
check("project deleted (cascade: items, rooms, measurements, schedule docs)", st == 200, "removed FAT project")
st, r = call(f"/api/projects/{PID}", token=ATOK)
check("deleted project no longer visible", st == 404)
st, r = call("/api/csr/versions?fy=2025-26&region=Nashik", "DELETE", token=ATOK)
check("test CSR version removed", st == 200)
st, r = call(f"/api/admin/users/{NEW_UID}", "DELETE", token=ATOK)
check("test user removed", st == 200)
st, r = call("/api/admin/users/" + str(ATOK_UID), "DELETE", token=ATOK)
check("admin cannot delete own account", st == 400)
st, r = call("/api/health")
check("master DB back to its seeded size", r["items"] == 2268, f"{r['items']} rows")

print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("failed checks:\n  - " + "\n  - ".join(FAIL))
sys.exit(1 if FAIL else 0)
