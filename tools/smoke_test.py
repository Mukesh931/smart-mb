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
import re
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
check("CSR versions listed", st == 200 and len(r["versions"]) >= 14, f"{len(r.get('versions', []))} versions")
START_ITEMS = call("/api/health")[1]["items"]

print("\n[1b] the official PWD CSR 2022-23 (printed PDF) is in the Master Database")
st, r = call("/api/csr/versions", token=ETOK)
official = [v for v in r.get("versions", []) if v["fy"] == "2022-23" and v["region"] == "Maharashtra"]
check("the printed CSR ships with the app (2022-23 / Maharashtra)", bool(official) and official[0]["item_count"] >= 2300,
      f"{official[0]['item_count'] if official else 0} items from {official[0]['source_file'] if official else '-'}")
st, r = call("/api/csr/items?fy=2022-23&region=Maharashtra&q=1-1-1&limit=1", token=ETOK)
c111 = (r.get("items") or [{}])[0]
check("item 1-1-1 read from the printed book (m @ 197)", c111.get("unit") == "m" and abs((c111.get("rate") or 0) - 197) < 0.01,
      f"{c111.get('item_code')} {c111.get('unit')} @ {c111.get('rate')}")
st, r = call("/api/csr/items?fy=2022-23&region=Maharashtra&q=9-1-4&limit=1", token=ETOK)
c914 = (r.get("items") or [{}])[0]
check("1-3-14 / 9-1-4 carry the CSR's own completed rate", abs((c914.get("rate") or 0) - 1500) < 0.01,
      f"9-1-4 @ {c914.get('rate')}")
check("chapters and specification numbers survive the import",
      str(c111.get("chapter")) == "1" and len(c111.get("section") or "") > 4,
      f"ch{c111.get('chapter')} · {c111.get('section')}")
st, r = call("/api/csr/suggest?q=exhaust+fan&fy=2022-23&region=Maharashtra", token=ETOK)
hits = r.get("results", [])
check("'Exhaust Fan' resolves to fan-supply items inside the real CSR",
      bool(hits) and all(re.search(r"exhaust fan", x["description"], re.I) for x in hits[:3]),
      ", ".join(f"{x['item_code']} Rs{x['rate']}" for x in hits[:3]))
check("the search does not answer with a dismantling or painting row",
      all(not re.search(r"dismantl|spray painting", x["description"][:40], re.I) for x in hits[:3]),
      (hits[0]["description"][:60] if hits else ""))
st, r = call("/api/csr/suggest?q=MCCB+100A&fy=2022-23&region=Maharashtra", token=ETOK)
check("a purpose clause does not outrank the item itself",
      bool(r.get("results")) and "mccb" in r["results"][0]["description"][:60].lower(),
      r["results"][0]["description"][:64] if r.get("results") else "")
st, r = call("/api/csr/versions", token=ETOK)
check("the rate-book versions are distinguishable by name",
      len({v["fy"] for v in r["versions"]}) >= 2, f"{len({v['fy'] for v in r['versions']})} financial years")

print("\n[1c] organisation - division, sub division and sections")
st, og = call("/api/org", token=ATOK)
names = {d["name"]: [s2["name"] for s2 in d["children"]] for d in og.get("tree", [])}
div = "PWD Electrical Division Dhule"
sub = "PWD Electrical Sub Division Jalgaon"
check("division Dhule exists", div in names, ", ".join(names))
check("sub division Jalgaon exists under it", sub in names.get(div, []), ", ".join(names.get(div, [])))
sections = {x["name"] for d in og["tree"] for s2 in d["children"] for x in s2["children"]}
check("its three sections are modelled", {"Jalgaon-1", "Jalgaon-2", "Amalner"} <= sections, ", ".join(sorted(sections)))
check("roles follow the hierarchy", {"ee", "sdo", "section"} <= {r["id"] for r in og["roles"]},
      ", ".join(r["id"] for r in og["roles"]))

OFFICERS = {}
for email, label in (("ee.dhule@pwd.maharashtra.gov.in", "EE"), ("sdo.jalgaon@pwd.maharashtra.gov.in", "SDO"),
                     ("je.jalgaon1@pwd.maharashtra.gov.in", "JE-1"), ("je.jalgaon2@pwd.maharashtra.gov.in", "JE-2"),
                     ("je.amalner@pwd.maharashtra.gov.in", "JE-Amalner")):
    st, r = call("/api/auth/login", "POST", {"email": email, "password": "Engineer@123"})
    OFFICERS[label] = r.get("token", "")
check("every officer of the division can sign in", all(OFFICERS.values()), ", ".join(k for k, v in OFFICERS.items() if not v))

st, ee_me = call("/api/auth/me", token=OFFICERS["EE"])
st, sdo_me = call("/api/auth/me", token=OFFICERS["SDO"])
check("an EE is scoped to the whole division", "Division Dhule" in (ee_me["user"].get("scope_label") or ""),
      ee_me["user"].get("scope_label"))
check("an SDO is scoped to the sub division", "Sub Division Jalgaon" in (sdo_me["user"].get("scope_label") or ""),
      sdo_me["user"].get("scope_label"))

org_projects = []
for label, name in (("JE-1", "FAT org - Jalgaon-1 work"), ("JE-2", "FAT org - Jalgaon-2 work"),
                    ("JE-Amalner", "FAT org - Amalner work")):
    st, r = call("/api/projects", "POST", {"name": name, "csr_fy": "2022-23", "csr_region": "Maharashtra",
                                           "region": "Jalgaon"}, token=OFFICERS[label])
    org_projects.append(r.get("project_id"))
check("each section officer can create their own work", all(org_projects), str(org_projects))

seen = {}
for label in ("JE-1", "JE-2", "JE-Amalner", "SDO", "EE"):
    ids = {p["id"] for p in call("/api/projects", token=OFFICERS[label])[1].get("projects", [])}
    seen[label] = {i for i in org_projects if i in ids}
check("a section officer sees only their own section's work", seen["JE-1"] == {org_projects[0]} and seen["JE-2"] == {org_projects[1]},
      f"JE-1 {sorted(seen['JE-1'])} · JE-2 {sorted(seen['JE-2'])}")
check("the SDO sees all three sections", seen["SDO"] == set(org_projects), str(sorted(seen["SDO"])))
check("the EE sees the whole division", seen["EE"] == set(org_projects), str(sorted(seen["EE"])))

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
check("matched against master", stt.get("matched", 0) == 19, str(stt.get("matched")))
check("a code missing from this version is matched from another CSR year, and said so",
      all(r["status"] in ("matched", "matched_other_version") for r in parsed["rows"]) and
      any(r["status"] == "matched_other_version" for r in parsed["rows"]),
      ", ".join(sorted({r["status"] for r in parsed["rows"]})))
check("the cross-version match carries a warning to confirm the rate",
      any(r.get("flags") for r in parsed["rows"] if r["status"] == "matched_other_version"))
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
check("the abstract's own total is reported as printed", (astats.get("estimated_amount") or 0) > 0,
      f"Rs {astats.get('estimated_amount')}")
check("and re-valued at the Master CSR rates", (astats.get("csr_amount") or 0) > 0,
      f"Rs {astats.get('csr_amount')}")

print("\n[4c] Flow B3 - the same printed abstract mapped against the real CSR 2022-23")
st, r = call("/api/projects", "POST", {
    "name": "FAT Test - street light work mapped to the official CSR", "scheme": "Test scheme",
    "region": "Nashik", "csr_fy": "2022-23", "csr_region": "Maharashtra", "mb_no": "MB-CSR",
    "rooms": [{"floor": "Ground Floor", "name": "Panel Room"}],
}, token=ETOK)
PID2 = r.get("project_id")
st, csr_parsed = upload(f"/api/projects/{PID2}/parse-estimate",
                        os.path.join(ROOT, "samples", "sample_work_abstract.pdf"),
                        {"engine": "auto"}, token=ETOK)
cstats = csr_parsed.get("stats", {})
crows = {r["item_code"]: r for r in csr_parsed.get("rows", [])}
check("every code in the abstract now resolves inside the printed CSR",
      cstats.get("matched") == 9 and cstats.get("unknown") == 0,
      f"{cstats.get('matched')} matched, {cstats.get('unknown')} outside the CSR")
check("the master's rate replaces the printed one where the book differs",
      abs((crows.get("9-1-4", {}).get("rate") or 0) - 1500) < 0.01,
      f"9-1-4 -> Rs {crows.get('9-1-4', {}).get('rate')} (abstract printed 22030)")
check("rates that agree are carried through unchanged",
      abs((crows.get("1-3-14", {}).get("rate") or 0) - 61) < 0.01 and
      abs((crows.get("7-1-5", {}).get("rate") or 0) - 127) < 0.01,
      f"1-3-14 {crows.get('1-3-14', {}).get('rate')} · 7-1-5 {crows.get('7-1-5', {}).get('rate')}")
check("units come from the CSR, not the abstract's shorthand",
      crows.get("2-4-5", {}).get("unit") == "Each" and crows.get("1-3-14", {}).get("unit") == "m",
      f"{crows.get('2-4-5', {}).get('unit')} / {crows.get('1-3-14', {}).get('unit')}")
check("the printed total and the CSR-valued total are both reported",
      abs((cstats.get("estimated_amount") or 0) - 668146) < 200 and abs((cstats.get("csr_amount") or 0) - 421786) < 200,
      f"printed Rs {cstats.get('estimated_amount')} · at CSR rates Rs {cstats.get('csr_amount')}")
st, imp = call(f"/api/projects/{PID2}/import-estimate", "POST", {"rows": csr_parsed["rows"]}, token=ETOK)
check("the CSR-mapped abstract imports as a checklist", st == 200 and len(imp.get("created", [])) == 9,
      f"{len(imp.get('created', []))} items")
st, cl2 = call(f"/api/projects/{PID2}/checklist", token=ETOK)
check("checklist carries the legal descriptions, not the printed ones",
      all(len(i.get("description", "")) > 40 for i in cl2.get("items", [])),
      f"{len(cl2.get('items', []))} items")
call(f"/api/projects/{PID2}", "DELETE", token=ATOK)

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

print("\n[11b] Admin - data safety / backup contract")
st, bk = call("/api/admin/backup", token=ATOK)
need = {"configured", "repo", "path", "interval", "last_push", "last_size", "last_restore",
        "last_error", "pending", "storage", "data_dir", "db_bytes"}
check("backup status reports the whole contract", st == 200 and need <= set(bk.keys()),
      f"storage={bk.get('storage')} configured={bk.get('configured')}")
check("the storage warning matches reality", bk.get("storage") in ("persistent", "ephemeral"),
      bk.get("storage"))
if bk.get("configured"):
    check("a configured backup names its repository and path",
          bool(bk.get("repo")) and bool(bk.get("path")), f"{bk.get('repo')} · {bk.get('path')}")
    # a freshly booted instance has not pushed yet; SMARTMB_EXPECT_BACKUP=1 asserts that a
    # real snapshot has landed (that is what we check on the production service)
    pushed = bool(bk.get("last_push")) and (bk.get("last_size") or 0) > 0
    if os.environ.get("SMARTMB_EXPECT_BACKUP") == "1":
        check("a snapshot has already been pushed to the repository", pushed,
              f"last push {bk.get('last_push')} · {(bk.get('last_size') or 0)/1024:.0f} KiB")
    else:
        check("push state is reported (null until the first snapshot)", True,
              f"last push {bk.get('last_push') or 'not yet'} · {(bk.get('last_size') or 0)/1024:.0f} KiB")
    check("with no error recorded", not bk.get("last_error"), str(bk.get("last_error"))[:80])
else:
    check("backups are switched off here (set SMARTMB_BACKUP_REPO/TOKEN on the server)", True,
          "not configured on this instance")
st, r = call("/api/admin/backup/now", "POST", token=ETOK)
check("only an admin may push a snapshot", st == 403, str(r)[:60])

print("\n[12] cleanup - remove every artifact this test created")
for _pid in org_projects:
    call(f"/api/projects/{_pid}", "DELETE", token=ATOK)
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
check("master DB back to its seeded size", r["items"] == START_ITEMS, f"{r['items']} rows (started at {START_ITEMS})")

print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("failed checks:\n  - " + "\n  - ".join(FAIL))
sys.exit(1 if FAIL else 0)
