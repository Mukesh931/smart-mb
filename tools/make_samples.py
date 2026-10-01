"""
Builds the sample input files used in the demo (and by the automated test):
  samples/sample_estimate.xlsx        - clean estimate abstract (Excel)
  samples/sample_estimate.csv         - same, CSV
  samples/sample_estimate.pdf         - "scanned-looking" abstract PDF, deliberately
                                        messy: wrapped rows, drifting columns, one OCR
                                        artefact (1-O-1 instead of 1-1-1) and one
                                        non-schedule code (11-2-1) that is NOT in the
                                        Master CSR -> tests the fallback path.

Run:  python3 -m tools.make_samples   (from the smart-mb directory)
"""
from __future__ import annotations

import csv
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "samples")
os.makedirs(SAMPLES, exist_ok=True)

# (item code, description as it would appear in a badly formatted estimate, unit, qty)
ROWS = [
    ("3-1-1", "Supplying and erecting ceiling fan 1200 mm sweep 5 star with down rod accessories", "No", 16),
    ("1-9-1", "Concealed type light point wiring with 1.5 sq.mm (1.5+1E) FR copper wire in provided concealed pipes", "Point", 84),
    ("1-9-2", "Concealed type independent plug point wiring 2.5 sq.mm with modular accessories", "Point", 22),
    ("1-9-6", "Concealed ceiling fan point wiring 1.5 sq.mm (2+1E) FR copper with hook box", "Point", 16),
    ("1-9-10", "Modular electronic fan regulator with 2M plate GI box", "No", 16),
    ("2-1-3", "LED batten luminaire 20 W 4 ft 6500 K polycarbonate body with driver", "No", 96),
    ("2-1-1", "LED panel luminaire 18 W 1200x300 mm surface mounted 6500 K", "No", 42),
    ("1-1-1", "Supplying & laying 20 mm dia PVC conduit concealed in wall / ceiling with bends & couplers", "m", 620),
    ("1-1-2", "Supplying and laying 25 mm dia. PVC conduit concealed in wall / ceiling", "m", 340),
    ("1-3-3", "S&E mains with 2x4 sq.mm FRLSH copper PVC insulated wire in provided conduit", "m", 260),
    ("1-3-6", "S&E mains with 3x4 sq.mm FRLSH copper PVC insulated wire in provided conduit", "m", 190),
    ("6-1-1", "S&F SPN distribution board 8 way single door with copper bus bar", "No", 4),
    ("6-2-1", "S&F MCB single pole 6A to 32A C curve ISI marked", "No", 48),
    ("6-2-4", "S&F RCCB double pole 40A 30 mA sensitivity", "No", 6),
    ("7-2-4", "S/E&T FR XLPE armoured cable 1100 V 3.5 core 16 sq.mm copper with glands and lugs", "m", 120),
    ("9-1-1", "Providing earthing with GI earth plate 60x60x0.6 cm with salt charcoal and chamber", "No", 3),
    ("9-2-3", "S&F GI strip 25x6 mm as earth continuity conductor with clamps", "m", 90),
    ("14-3-1", "Testing & commissioning of internal installation including megger test per point", "Point", 320),
    ("11-2-1", "Providing and fixing 40 W LED street light luminaire with programmable driver (non-schedule / new item)", "No", 6),
]


def make_csv() -> str:
    path = os.path.join(SAMPLES, "sample_estimate.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Item No", "Description", "Unit", "Rate", "Quantity", "Amount"])
        for code, desc, unit, qty in ROWS:
            w.writerow([code, desc, unit, "", qty, ""])
    return path


def make_xlsx() -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    path = os.path.join(SAMPLES, "sample_estimate.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Abstract of Estimate"
    ws["A1"] = "PUBLIC WORKS DEPARTMENT (ELECTRICAL) - MAHARASHTRA"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Abstract of Technical Sanction Estimate"
    ws["A3"] = "Name of Work: Electrical Installation to New Academic Building, Zilla Parishad High School, Sinnar"
    ws["A4"] = "Estimate No: EST/ELE/NSK/2024-25/017        TS No: TS/ELE/NSK/2024-25/041"
    ws.append([])
    ws.append(["Item No", "Description", "Unit", "Rate", "Quantity", "Amount"])
    hrow = ws.max_row
    for c in range(1, 7):
        cell = ws.cell(row=hrow, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B5C8A")
    for code, desc, unit, qty in ROWS:
        ws.append([code, desc, unit, "", qty, ""])
    ws.append([])
    ws.append(["", "TOTAL", "", "", "", ""])
    for i, w in enumerate([12, 78, 9, 12, 12, 14], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    wb.save(path)
    return path


def make_pdf() -> str:
    """A deliberately messy PDF: 3-1-5 style artefacts, wrapped rows, extra rate columns."""
    path = os.path.join(SAMPLES, "sample_estimate.pdf")
    ss = getSampleStyleSheet()
    cell = ParagraphStyle("c", parent=ss["Normal"], fontSize=7.4, leading=9)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7.6, leading=10)
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=12 * mm)
    flow = [
        Paragraph("<b>GOVERNMENT OF MAHARASHTRA - PUBLIC WORKS DEPARTMENT (ELECTRICAL WING)</b>", ss["Title"]),
        Paragraph("Abstract of Technical Sanction Estimate - Electrical Installation to New Academic Building, "
                  "Zilla Parishad High School, Sinnar, Dist. Nashik", ss["Normal"]),
        Paragraph("Estimate No: EST/ELE/NSK/2024-25/017 &nbsp;&nbsp; TS No: TS/ELE/NSK/2024-25/041 &nbsp;&nbsp; "
                  "dt. 19/08/2024 &nbsp;&nbsp; CSR 2024-25 (Nashik region)", small),
        Spacer(1, 5 * mm),
    ]
    data = [[Paragraph(f"<b>{h}</b>", cell) for h in
             ["Item No", "Short Description", "Unit", "Material Rate", "Labour Rate", "Rate",
              "Quantity", "Amount"]]]
    for code, desc, unit, qty in ROWS:
        printed = "1-O-1" if code == "1-1-1" else code  # simulated OCR artefact
        data.append([Paragraph(printed, cell), Paragraph(desc, cell), Paragraph(unit, cell),
                     Paragraph("", cell), Paragraph("", cell), Paragraph("", cell),
                     Paragraph(str(qty), cell), Paragraph("", cell)])
        if code == "1-1-1":
            data.append([Paragraph("", cell),
                         Paragraph("<i>(contd.) with necessary bends, couplers, junction boxes, cutting and making "
                                   "good the wall complete as per specification No: WG-CP/PVC.</i>", cell),
                         Paragraph("", cell), Paragraph("", cell), Paragraph("", cell), Paragraph("", cell),
                         Paragraph("", cell), Paragraph("", cell)])
    data.append([Paragraph("", cell), Paragraph("<b>TOTAL</b>", cell), Paragraph("", cell), Paragraph("", cell),
                 Paragraph("", cell), Paragraph("", cell), Paragraph("", cell), Paragraph("", cell)])
    data.append([Paragraph("", cell),
                 Paragraph("<i>Note: Item No. 11-2-1 is a new item approved by the competent authority and is not "
                           "covered in the current Schedule of Rates - rate to be supported by market quotation.</i>",
                           cell),
                 Paragraph("", cell), Paragraph("", cell), Paragraph("", cell), Paragraph("", cell),
                 Paragraph("", cell), Paragraph("", cell)])
    tbl = Table(data, colWidths=[16 * mm, 118 * mm, 14 * mm, 22 * mm, 22 * mm, 20 * mm, 18 * mm, 24 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#8899aa")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B5C8A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(tbl)
    doc.build(flow)
    return path


def make_csr_template() -> str:
    """A blank Master CSR import template the Admin can fill with the official file."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    path = os.path.join(SAMPLES, "MasterCSR_import_template.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Master CSR"
    headers = ["Item No", "Description", "Unit", "Material Rate", "Labour Rate", "Completed Rate",
               "Chapter", "Section", "Category", "Spec No", "Tags"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B5C8A")
    ws.append(["1-1-1", "Supplying and laying 20 mm dia. PVC conduit concealed in wall ...",
               "m", 75, 21, 96, 1, "1.1 Conduit & accessories", "Internal Wiring", "WG-CP/PVC", "Conduit"])
    ws.append(["1-3-6", "Supplying and erecting mains with 3x4 sq.mm FRLSH copper PVC insulated wire ...",
               "m", 170, 48, 218, 1, "1.3 Bunch of wires", "Mains", "WG-MA/BW", "Wiring,Copper"])
    for i, w in enumerate([12, 78, 9, 14, 13, 15, 9, 30, 18, 14, 24], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def make_all() -> dict:
    return {"csv": make_csv(), "xlsx": make_xlsx(), "pdf": make_pdf(), "csr_template": make_csr_template()}


if __name__ == "__main__":
    for k, v in make_all().items():
        print(f"{k:14s} -> {v}")
