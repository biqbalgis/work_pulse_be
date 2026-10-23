"""
Blank Daily Cost Sheet (paper LEM, template 2) PDF for Bigstone Oilfield Services & Supplies Ltd.

A copy of the client cost-sheet form, meant to be printed and filled in by hand in the field. Only the
ticket number and date are filled in (both assigned when the sheet is downloaded); every other field,
including the supervisor signature, is left blank.
"""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .daily_ticket_pdf_utils import INK, LOGO_PATH, NAVY, _text

COMPANY_NAME = "Bigstone Oilfield Services & Supplies Ltd."
COMPANY_ADDRESS = ["#166a, Wabasca-Desmarais, Alberta", "T0G 2K0"]
COMPANY_PHONE = "780-891-2677"

PAGE_W, PAGE_H = letter
LEFT, RIGHT = 24, PAGE_W - 24
WIDTH = RIGHT - LEFT
ROW_H = 11.5
LINE_W = 0.6

HEADER_BG = colors.HexColor("#d9d9d9")
AMOUNT_BG = colors.HexColor("#e6eef9")  # the form's tinted AMOUNT / CVE CODE 4 columns
SUMMARY_BG = colors.HexColor("#2f6f7e")

# The three tables share their right-hand columns so AMOUNT / CVE CODE 4 line up down the page.
LABOUR_COLS = [
    ("EMPLOYEE NAME", 80), ("CLASSIFICATION", 62), ("ACTIVITY", 62), ("UOM", 36), ("RATE", 44),
    ("HOURS /\nDAYS", 40), ("SERVICE TRUCK", 62), ("SUBSISTENCE", 62), ("AMOUNT", 62), ("CVE CODE 4", 54),
]
EQUIPMENT_COLS = [
    ("EQUIPMENT TYPE", 80), ("UNIT", 62), ("ACTIVITY", 62), ("UOM", 36), ("RATE", 44),
    ("HOURS /\nDAYS", 40), ("OPERATOR NAME", 124), ("AMOUNT", 62), ("CVE CODE 4", 54),
]
MATERIAL_COLS = [
    ("MATERIALS / THIRD-PARTY", 142), ("ACTIVITY", 62), ("QUANTITY", 36), ("COST", 44),
    ("MARKUP\n(5%)", 40), ("DESCRIPTION", 124), ("AMOUNT", 62), ("CVE CODE 4", 54),
]
SHADED = {"AMOUNT", "CVE CODE 4"}


def _table(c, y_top, columns, rows, header_h=16):
    """Column-heading row plus `rows` empty writing rows. Returns the y of the bottom edge."""
    x = LEFT
    body_top = y_top - header_h
    y_bottom = body_top - rows * ROW_H

    for label, width in columns:
        if label in SHADED:
            c.setFillColor(AMOUNT_BG)
            c.rect(x, y_bottom, width, body_top - y_bottom, stroke=0, fill=1)
        c.setFillColor(HEADER_BG)
        c.rect(x, body_top, width, header_h, stroke=0, fill=1)
        lines = label.split("\n")
        first_y = body_top + header_h / 2 + (len(lines) - 1) * 3.2 - 2
        for i, line in enumerate(lines):
            _text(c, x + width / 2, first_y - i * 6.4, line, size=5.8, bold=True, align="center")
        x += width

    c.setStrokeColor(INK)
    c.setLineWidth(LINE_W)
    c.rect(LEFT, y_bottom, WIDTH, y_top - y_bottom)
    c.line(LEFT, body_top, RIGHT, body_top)
    c.setLineWidth(0.3)
    for i in range(1, rows):
        c.line(LEFT, body_top - i * ROW_H, RIGHT, body_top - i * ROW_H)
    x = LEFT
    for _, width in columns[:-1]:
        x += width
        c.line(x, y_top, x, y_bottom)
    c.setLineWidth(LINE_W)
    return y_bottom


def _subtotal(c, y_top, columns, label):
    """Bold subtotal line under a table: label right-aligned before the AMOUNT cell."""
    amount_x = RIGHT - columns[-1][1] - columns[-2][1]
    cve_x = RIGHT - columns[-1][1]
    y_bottom = y_top - ROW_H - 1
    c.setFillColor(AMOUNT_BG)
    c.rect(amount_x, y_bottom, RIGHT - amount_x, y_top - y_bottom, stroke=0, fill=1)
    c.setStrokeColor(INK)
    c.rect(amount_x, y_bottom, RIGHT - amount_x, y_top - y_bottom)
    c.line(cve_x, y_top, cve_x, y_bottom)
    _text(c, amount_x - 5, y_bottom + 3.5, label, size=6.5, bold=True, align="right")
    return y_bottom


def _info_grid(c, y_top, rows, label_w, split, right_label_w, row_h=13):
    """Two-column label/value grid. Each row is (left_label, right_label, right_value); a row whose
    right_label is None spans the full width."""
    y = y_top
    c.setLineWidth(LINE_W)
    for left_label, right_label, right_value in rows:
        y_next = y - row_h
        c.rect(LEFT, y_next, WIDTH, row_h)
        _text(c, LEFT + 3, y_next + 4, left_label, size=6.5, bold=True)
        c.setLineWidth(0.3)
        c.line(LEFT + label_w, y, LEFT + label_w, y_next)
        if right_label is not None:
            c.line(split, y, split, y_next)
            c.line(split + right_label_w, y, split + right_label_w, y_next)
            _text(c, split + 3, y_next + 4, right_label, size=6.5, bold=True)
            if right_value:
                _text(c, split + right_label_w + 4, y_next + 3, right_value, size=8.5, bold=True, color=NAVY)
        c.setLineWidth(LINE_W)
        y = y_next
    return y


def generate_cost_sheet_pdf(ticket_number: str, ticket_date: str) -> io.BytesIO:
    """
    ticket_number: e.g. "26-00001" (printed as "26 - 00001")
    ticket_date:   already formatted for printing, e.g. "Oct 23, 2026"
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"Daily Cost Sheet {ticket_number}")
    c.setAuthor(COMPANY_NAME)
    c.setStrokeColor(INK)
    c.setLineWidth(LINE_W)

    # ── Header: logo | title | company details ───────────────────────────
    top = PAGE_H - 22
    logo = ImageReader(LOGO_PATH)
    logo_w_px, logo_h_px = logo.getSize()
    logo_w = 150
    logo_h = logo_w * logo_h_px / logo_w_px
    c.drawImage(logo, LEFT, top - logo_h, width=logo_w, height=logo_h)

    _text(c, 290, top - logo_h / 2 - 6, "DAILY COST SHEET", size=17, bold=True, align="center")

    details_x = 420
    details = [("Company:", COMPANY_NAME), ("Address:", COMPANY_ADDRESS[0]), ("", COMPANY_ADDRESS[1]),
               ("Phone:", COMPANY_PHONE)]
    y = top - 12
    for label, value in details:
        if label:
            _text(c, details_x, y, label, size=6.5, bold=True)
        size = 5.9 if value == COMPANY_NAME else 6.5
        _text(c, details_x + 33, y, value, size=size, bold=(value == COMPANY_NAME))
        y -= 9.5

    # ── Ticket information ───────────────────────────────────────────────
    yy, _, seq = ticket_number.partition("-")
    info_top = top - logo_h - 6
    y = _info_grid(
        c, info_top,
        [
            ("CUSTOMER:", "TICKET#:", f"{yy} - {seq}"),
            ("ATTENTION:", "PREPARED BY:", ""),
            ("LOCATION:", "PO#:", ""),
            ("DATE:", "PROJECT #:", ""),
            ("DESCRIPTION:", None, None),
            ("", None, None),
        ],
        label_w=62, split=360, right_label_w=62,
    )
    # The date is part of the left column (row 4).
    _text(c, LEFT + 66, info_top - 4 * 13 + 3, ticket_date, size=8.5, bold=True, color=NAVY)

    # ── Labour / equipment / materials ───────────────────────────────────
    y = _table(c, y - 6, LABOUR_COLS, 14)
    y = _subtotal(c, y, LABOUR_COLS, "EMPLOYEE SUBTOTAL:")
    y = _table(c, y - 6, EQUIPMENT_COLS, 10)
    y = _subtotal(c, y, EQUIPMENT_COLS, "EQUIPMENT SUBTOTAL:")
    y = _table(c, y - 6, MATERIAL_COLS, 6)
    y = _subtotal(c, y, MATERIAL_COLS, "MATERIALS / SUPPLIES / THIRD-PARTY SUBTOTAL:")

    # ── CVE CODE 4 summary + subtotal ────────────────────────────────────
    summary_top = y - 8
    summary_w = 330
    band_h = 10
    cols = 6
    col_w = summary_w / cols
    summary_rows = 3  # activity, CVE code, amount
    c.setFillColor(SUMMARY_BG)
    c.rect(LEFT, summary_top - band_h, summary_w, band_h, stroke=0, fill=1)
    _text(c, LEFT + summary_w / 2, summary_top - band_h + 3, "CVE CODE 4 SUMMARY", size=6.5, bold=True,
          align="center", color=colors.white)
    body_top = summary_top - band_h
    summary_bottom = body_top - summary_rows * ROW_H
    c.setStrokeColor(INK)
    c.rect(LEFT, summary_bottom, summary_w, summary_top - summary_bottom)
    c.setLineWidth(0.3)
    for i in range(1, summary_rows):
        c.line(LEFT, body_top - i * ROW_H, LEFT + summary_w, body_top - i * ROW_H)
    for i in range(1, cols):
        c.line(LEFT + i * col_w, body_top, LEFT + i * col_w, summary_bottom)
    c.setLineWidth(LINE_W)

    box_x1 = RIGHT - 140
    box_h = summary_top - summary_bottom
    _text(c, box_x1 - 8, summary_bottom + box_h / 2 - 3, "SUBTOTAL:", size=9, bold=True, align="right")
    c.rect(box_x1, summary_bottom, RIGHT - box_x1, box_h)

    # ── Bill to + approval details ───────────────────────────────────────
    bill_y = summary_bottom - 14
    _text(c, PAGE_W / 2 - 60, bill_y, "Bill to:", size=7.5, bold=True, align="right")
    c.setLineWidth(0.4)
    c.line(PAGE_W / 2 - 55, bill_y - 2, PAGE_W / 2 + 110, bill_y - 2)

    left_fields = ["Attention:", "Invoice Contact / Department:", "Site Name:", "Job Name:", "Service Date:",
                   "WBS / AFE:", "GL Code:", "MG Code:"]
    right_fields = ["Vendor:", "Commitment (PO / MSG):", "Amount:", "Supervisor Name:", "Supervisor Signature:",
                    "Additional Contact:", "Ticket Approver:"]
    line_gap = 10.5
    first_y = bill_y - 16
    for fields, label_right_x, line_end_x in ((left_fields, 150, 290), (right_fields, 400, RIGHT - 20)):
        for i, label in enumerate(fields):
            fy = first_y - i * line_gap
            _text(c, label_right_x, fy, label, size=6.5, bold=True, align="right")
            c.line(label_right_x + 4, fy - 1.5, line_end_x, fy - 1.5)
            if label == "Vendor:":
                _text(c, label_right_x + 6, fy, COMPANY_NAME, size=6.5)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
