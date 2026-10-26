"""
Daily Cost Sheet (paper LEM, template 2) PDF for Bigstone Oilfield Services & Supplies Ltd.

A copy of the client cost-sheet form. Used two ways:
  - blank, to print and fill in by hand in the field: only the ticket number and date are filled in;
  - filled, from a submitted field ticket: everything the system knows is filled in, the rest
    (PO#, CVE codes, materials, supervisor signature, ...) is left blank to complete by hand.
"""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .daily_ticket_pdf_utils import INK, LOGO_PATH, NAVY, SUN, _fit, _text, _wrap, draw_page_count, paginate

COMPANY_NAME = "Bigstone Oilfield Services & Supplies Ltd."
COMPANY_ADDRESS = ["#166a, Wabasca-Desmarais, Alberta", "T0G 2K0"]
COMPANY_PHONE = "780-891-2677"

PAGE_W, PAGE_H = letter
LEFT, RIGHT = 24, PAGE_W - 24
WIDTH = RIGHT - LEFT
ROW_H = 11.5
LINE_W = 0.6
CELL_SIZE = 6.5

HEADER_BG = colors.HexColor("#d9d9d9")
AMOUNT_BG = colors.HexColor("#e6eef9")  # the form's tinted AMOUNT / CVE CODE 4 columns
SUMMARY_BG = colors.HexColor("#2f6f7e")

LABOUR_ROWS = 14
EQUIPMENT_ROWS = 10

# The three tables share their right-hand columns so AMOUNT / CVE CODE 4 line up down the page.
# Each column: (heading, width, filled-data key or None, alignment).
LABOUR_COLS = [
    ("EMPLOYEE NAME", 80, "name", "left"), ("CLASSIFICATION", 62, "classification", "left"),
    ("ACTIVITY", 62, "activity", "left"), ("UOM", 36, "uom", "center"), ("RATE", 44, "rate", "right"),
    ("HOURS /\nDAYS", 40, "hours", "center"), ("SERVICE TRUCK", 62, None, "left"),
    ("SUBSISTENCE", 62, "subsistence", "left"), ("AMOUNT", 62, "amount", "right"), ("CVE CODE 4", 54, None, "left"),
]
EQUIPMENT_COLS = [
    ("EQUIPMENT TYPE", 80, "name", "left"), ("UNIT", 62, None, "left"), ("ACTIVITY", 62, "activity", "left"),
    ("UOM", 36, "uom", "center"), ("RATE", 44, "rate", "right"), ("HOURS /\nDAYS", 40, "quantity", "center"),
    ("OPERATOR NAME", 124, "operator", "left"), ("AMOUNT", 62, "amount", "right"), ("CVE CODE 4", 54, None, "left"),
]
MATERIAL_COLS = [
    ("MATERIALS / THIRD-PARTY", 142, None, "left"), ("ACTIVITY", 62, None, "left"), ("QUANTITY", 36, None, "left"),
    ("COST", 44, None, "left"), ("MARKUP\n(5%)", 40, None, "left"), ("DESCRIPTION", 124, None, "left"),
    ("AMOUNT", 62, None, "left"), ("CVE CODE 4", 54, None, "left"),
]
SHADED = {"AMOUNT", "CVE CODE 4"}


def _table(c, y_top, columns, rows, data_rows=(), header_h=16):
    """Column-heading row plus `rows` writing rows, the first ones filled from `data_rows`.
    Returns the y of the bottom edge."""
    x = LEFT
    body_top = y_top - header_h
    y_bottom = body_top - rows * ROW_H

    for label, width, _, _ in columns:
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
    for _, width, _, _ in columns[:-1]:
        x += width
        c.line(x, y_top, x, y_bottom)
    c.setLineWidth(LINE_W)

    for row_index, row in enumerate(data_rows):
        base = body_top - row_index * ROW_H - 8.5
        x = LEFT
        for _, width, key, align in columns:
            value = _fit(row.get(key, ""), width - 4, CELL_SIZE) if key else ""
            if value:
                if align == "right":
                    _text(c, x + width - 2, base, value, size=CELL_SIZE, align="right")
                elif align == "center":
                    _text(c, x + width / 2, base, value, size=CELL_SIZE, align="center")
                else:
                    _text(c, x + 2, base, value, size=CELL_SIZE)
            x += width
    return y_bottom


def _subtotal(c, y_top, columns, label, amount=""):
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
    if amount:
        _text(c, cve_x - 2, y_bottom + 3.5, amount, size=CELL_SIZE, bold=True, align="right")
    return y_bottom


def _info_grid(c, y_top, rows, label_w, split, right_label_w, row_h=13):
    """Two-column label/value grid. Each row is (left_label, left_value, right_label, right_value); a row
    whose right_label is None spans the full width."""
    y = y_top
    c.setLineWidth(LINE_W)
    for left_label, left_value, right_label, right_value in rows:
        y_next = y - row_h
        c.rect(LEFT, y_next, WIDTH, row_h)
        _text(c, LEFT + 3, y_next + 4, left_label, size=6.5, bold=True)
        c.setLineWidth(0.3)
        c.line(LEFT + label_w, y, LEFT + label_w, y_next)
        left_end = RIGHT if right_label is None else split
        if left_value:
            _text(c, LEFT + label_w + 4, y_next + 3.5, _fit(left_value, left_end - LEFT - label_w - 8, 8, bold=True),
                  size=8, bold=True, color=NAVY)
        if right_label is not None:
            c.line(split, y, split, y_next)
            c.line(split + right_label_w, y, split + right_label_w, y_next)
            _text(c, split + 3, y_next + 4, right_label, size=6.5, bold=True)
            if right_value:
                _text(c, split + right_label_w + 4, y_next + 3,
                      _fit(right_value, RIGHT - split - right_label_w - 8, 8.5, bold=True), size=8.5, bold=True,
                      color=NAVY)
        c.setLineWidth(LINE_W)
        y = y_next
    return y


def generate_cost_sheet_pdf(ticket_number: str, ticket_date: str, filled: dict | None = None) -> io.BytesIO:
    """
    ticket_number: printed as given, e.g. "26-000001" or "26-000123-R01"
    ticket_date:   already formatted for printing, e.g. "Oct 23, 2026"
    filled:        a submitted ticket's data (see reports.boss_ticket_utils.build_boss_ticket_data), or None
                   for a blank sheet. Rows beyond one page continue on extra pages; totals go on the last page.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"Daily Cost Sheet {ticket_number}")
    c.setAuthor(COMPANY_NAME)

    pages = paginate(filled, LABOUR_ROWS, EQUIPMENT_ROWS)
    for page_no, (labour, equipment) in enumerate(pages, start=1):
        _draw_page(c, ticket_number, ticket_date, filled or {}, labour, equipment, is_last=page_no == len(pages))
        draw_page_count(c, page_no, len(pages))
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer


def _draw_page(c, ticket_number, ticket_date, filled, labour, equipment, is_last):
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
    if filled.get("non_billable"):
        _text(c, 290, top - logo_h / 2 - 22, "NON-BILLABLE · HOURS ONLY", size=9, bold=True, align="center", color=SUN)

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
    label_w = 62
    description = _wrap(filled.get("description", ""), RIGHT - LEFT - label_w - 8, 8, 2)
    description += [""] * (2 - len(description))
    y = _info_grid(
        c, top - logo_h - 6,
        [
            ("CUSTOMER:", filled.get("customer", ""), "TICKET#:", ticket_number),
            ("ATTENTION:", filled.get("attention", ""), "PREPARED BY:", filled.get("prepared_by", "")),
            ("LOCATION:", filled.get("location", ""), "PO#:", ""),
            ("DATE:", ticket_date, "PROJECT #:", filled.get("project_number", "")),
            ("DESCRIPTION:", description[0], None, None),
            ("", description[1], None, None),
        ],
        label_w=label_w, split=360, right_label_w=62,
    )

    # ── Labour / equipment / materials ───────────────────────────────────
    y = _table(c, y - 6, LABOUR_COLS, LABOUR_ROWS, labour)
    y = _subtotal(c, y, LABOUR_COLS, "EMPLOYEE SUBTOTAL:", filled.get("labour_total", "") if is_last else "")
    y = _table(c, y - 6, EQUIPMENT_COLS, EQUIPMENT_ROWS, equipment)
    y = _subtotal(c, y, EQUIPMENT_COLS, "EQUIPMENT SUBTOTAL:", filled.get("equipment_total", "") if is_last else "")
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
    if is_last and filled.get("subtotal"):
        _text(c, RIGHT - 8, summary_bottom + box_h / 2 - 5, filled["subtotal"], size=14, bold=True, align="right")

    # ── Bill to + approval details ───────────────────────────────────────
    bill_y = summary_bottom - 14
    _text(c, PAGE_W / 2 - 60, bill_y, "Bill to:", size=7.5, bold=True, align="right")
    if filled.get("customer"):
        _text(c, PAGE_W / 2 - 52, bill_y, _fit(filled["customer"], 160, 7.5, bold=True), size=7.5, bold=True)
    c.setLineWidth(0.4)
    c.line(PAGE_W / 2 - 55, bill_y - 2, PAGE_W / 2 + 110, bill_y - 2)

    values = {
        "Attention:": filled.get("attention", ""),
        "Job Name:": filled.get("location", ""),
        "Service Date:": ticket_date if filled else "",
        "Vendor:": COMPANY_NAME,
        "Amount:": filled.get("subtotal", "") if is_last else "",
    }
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
            if values.get(label):
                _text(c, label_right_x + 6, fy, _fit(values[label], line_end_x - label_right_x - 8, 6.5), size=6.5)
