"""
Daily Ticket (paper LEM, template 1) PDF for Bigstone Oilfield Services & Supplies Ltd.

A copy of the pre-printed ticket book page. Used two ways:
  - blank, to print and fill in by hand in the field: only the ticket number and date are filled in;
  - filled, from a submitted field ticket: everything the system knows is filled in, the rest
    (materials, GST, approval stamp, signature, ...) is left blank to complete by hand.
"""

import io
import math
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "reports", "images", "bigstone_logo.png")

COMPANY_CONTACT_LINES = [
    "Ph: 780-891-BOSS (2677)",
    "bossadmin@bigstone.ca",
    "Box 429",
    "Wabasca, AB. T0G 2K0",
]

PAGE_W, PAGE_H = letter
LEFT, RIGHT = 36, PAGE_W - 36
MID = (LEFT + RIGHT) / 2
ROW_H = 17
HEADER_H = 16
LINE_W = 0.8

INK = colors.black
FAINT = colors.HexColor("#9a9a9a")
NAVY = colors.HexColor("#2B4B9B")
SUN = colors.HexColor("#B4472A")  # logo sun orange (dark shade), for the NON-BILLABLE label

LABOUR_ROWS = 14
EQUIPMENT_ROWS = 4


def _text(c, x, y, value, size=8, bold=False, align="left", color=INK):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    if align == "center":
        c.drawCentredString(x, y, value)
    elif align == "right":
        c.drawRightString(x, y, value)
    else:
        c.drawString(x, y, value)


def _fit(text, width, size, bold=False):
    """Trim `text` with an ellipsis so it fits in `width` points."""
    text = str(text or "")
    font = "Helvetica-Bold" if bold else "Helvetica"
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _wrap(text, width, size, max_lines):
    """Word-wrap `text` into at most `max_lines` lines of `width` points; overflow is cut with an ellipsis."""
    words = str(text or "").split()
    lines, current = [], ""
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if stringWidth(candidate, "Helvetica", size) <= width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            lines[-1] = _fit(lines[-1] + " …", width, size)
            return lines
    if current:
        lines.append(current)
    return lines


def paginate(filled, labour_per_page, equipment_per_page):
    """Split a filled ticket's labour / equipment rows into pages: [(labour_rows, equipment_rows), ...]."""
    labour = (filled or {}).get("labour", [])
    equipment = (filled or {}).get("equipment", [])
    pages = max(1, math.ceil(len(labour) / labour_per_page), math.ceil(len(equipment) / equipment_per_page))
    return [
        (labour[i * labour_per_page:(i + 1) * labour_per_page],
         equipment[i * equipment_per_page:(i + 1) * equipment_per_page])
        for i in range(pages)
    ]


def draw_page_count(c, page_no, page_count):
    if page_count > 1:
        _text(c, PAGE_W - 24, 14, f"Page {page_no} of {page_count}", size=7.5, align="right", color=FAINT)


def _header_row(c, x1, x2, y_top, columns):
    """Bold column-heading row. `columns` is a list of (label, width); the last one takes the rest."""
    c.rect(x1, y_top - HEADER_H, x2 - x1, HEADER_H)
    x = x1
    for label, width in columns:
        _text(c, x + width / 2, y_top - HEADER_H + 5, label, size=7.5, bold=True, align="center")
        x += width
    return y_top - HEADER_H


def _grid(c, x1, x2, y_top, rows, column_widths):
    """Empty writing rows with vertical dividers after each width in `column_widths`."""
    y_bottom = y_top - rows * ROW_H
    c.rect(x1, y_bottom, x2 - x1, y_top - y_bottom)
    for i in range(1, rows):
        c.line(x1, y_top - i * ROW_H, x2, y_top - i * ROW_H)
    x = x1
    for width in column_widths:
        x += width
        c.line(x, y_top, x, y_bottom)
    return y_bottom


def _total_row(c, x1, x2, amount_x, y_top, label):
    """A labelled total line: label right-aligned before the amount box."""
    y_bottom = y_top - ROW_H
    c.rect(x1, y_bottom, x2 - x1, ROW_H)
    c.line(amount_x, y_top, amount_x, y_bottom)
    _text(c, amount_x - 6, y_bottom + 5.5, label, size=7.5, bold=True, align="right")
    return y_bottom


def generate_daily_ticket_pdf(ticket_number: str, ticket_date: str, filled: dict | None = None) -> io.BytesIO:
    """
    ticket_number: printed as given, e.g. "26-000001" or "26-000123-R01"
    ticket_date:   already formatted for printing, e.g. "Oct 23, 2026"
    filled:        a submitted ticket's data (see reports.boss_ticket_utils.build_boss_ticket_data), or None
                   for a blank ticket. Rows beyond one page continue on extra pages; totals go on the last page.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"Daily Ticket {ticket_number}")
    c.setAuthor("Bigstone Oilfield Services & Supplies Ltd.")

    pages = paginate(filled, LABOUR_ROWS, EQUIPMENT_ROWS)
    for page_no, (labour, equipment) in enumerate(pages, start=1):
        _draw_page(c, ticket_number, ticket_date, filled or {}, labour, equipment, is_last=page_no == len(pages))
        draw_page_count(c, page_no, len(pages))
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer


def _draw_page(c, ticket_number, ticket_date, filled, labour, equipment, is_last):
    c.setLineWidth(LINE_W)
    c.setStrokeColor(INK)

    # ── Letterhead ───────────────────────────────────────────────────────
    logo = ImageReader(LOGO_PATH)
    logo_w_px, logo_h_px = logo.getSize()
    logo_w = 230
    logo_h = logo_w * logo_h_px / logo_w_px
    logo_top = PAGE_H - 30
    c.drawImage(logo, LEFT, logo_top - logo_h, width=logo_w, height=logo_h)

    contact_x = LEFT + logo_w / 2
    y = logo_top - logo_h - 14
    for line in COMPANY_CONTACT_LINES:
        _text(c, contact_x, y, line, size=8.5, bold=(line == COMPANY_CONTACT_LINES[0]), align="center")
        y -= 10.5
    contact_bottom = y

    # ── Ticket box (top right) ───────────────────────────────────────────
    box_x1, box_x2 = 312, RIGHT
    box_top = PAGE_H - 52
    number_h = 36
    field_h = 27

    c.rect(box_x1, box_top - number_h, box_x2 - box_x1, number_h)
    _text(c, box_x1 + 10, box_top - number_h + 13, "DAILY TICKET NO.", size=11, bold=True)
    _text(c, box_x2 - 12, box_top - number_h + 11, _fit(ticket_number, 125, 18, bold=True), size=18, bold=True,
          align="right", color=NAVY)

    fields = [
        ("DATE", ticket_date, 11),
        ("JOB LOCATION", filled.get("location", ""), 9),
        ("EMPLOYEE", "", 9),
        ("CONSULTANT'S NAME & EMAIL", filled.get("consultant", ""), 9),
    ]
    y = box_top - number_h
    for label, value, size in fields:
        c.rect(box_x1, y - field_h, box_x2 - box_x1, field_h)
        _text(c, box_x1 + 5, y - 9, label, size=6.5, bold=True)
        if value:
            _text(c, box_x1 + 70, y - field_h + 7, _fit(value, box_x2 - box_x1 - 76, size, bold=True), size=size,
                  bold=True)
        y -= field_h
    box_bottom = y

    # ── TO: lines ────────────────────────────────────────────────────────
    to_y = contact_bottom - 8
    _text(c, LEFT, to_y + 2, "TO:", size=9, bold=True)
    if filled.get("customer"):
        _text(c, LEFT + 26, to_y + 3, _fit(filled["customer"], 290 - LEFT - 28, 10, bold=True), size=10, bold=True)
    c.setLineWidth(0.5)
    for i in range(3):
        line_y = to_y - i * 20
        c.line(LEFT + (22 if i == 0 else 0), line_y, 290, line_y)
    c.setLineWidth(LINE_W)
    to_bottom = to_y - 2 * 20

    # ── Description of work ──────────────────────────────────────────────
    desc_top = min(to_bottom, box_bottom) - 12
    desc_lines = 4
    desc_line_h = 19
    desc_bottom = desc_top - HEADER_H - desc_lines * desc_line_h
    c.rect(LEFT, desc_bottom, RIGHT - LEFT, desc_top - desc_bottom)
    c.line(LEFT, desc_top - HEADER_H, RIGHT, desc_top - HEADER_H)
    _text(c, MID, desc_top - HEADER_H + 5, "DESCRIPTION OF WORK", size=8, bold=True, align="center")
    if filled.get("non_billable"):
        _text(c, RIGHT - 6, desc_top - HEADER_H + 5, "NON-BILLABLE · HOURS ONLY", size=8, bold=True, align="right",
              color=SUN)
    c.setLineWidth(0.4)
    c.setStrokeColor(FAINT)
    for i in range(1, desc_lines):
        line_y = desc_top - HEADER_H - i * desc_line_h
        c.line(LEFT + 4, line_y, RIGHT - 4, line_y)
    c.setStrokeColor(INK)
    c.setLineWidth(LINE_W)
    for i, line in enumerate(_wrap(filled.get("description", ""), RIGHT - LEFT - 12, 9, desc_lines)):
        _text(c, LEFT + 6, desc_top - HEADER_H - i * desc_line_h - 13, line, size=9)

    # ── Labour (left) ────────────────────────────────────────────────────
    table_top = desc_bottom - 8
    split = 312
    labour_cols = [("RT", 28), ("OT", 28), ("EMPLOYEE", 112), ("PRICE", 50), ("AMOUNT", split - LEFT - 218)]
    labour_body_top = _header_row(c, LEFT, split, table_top, labour_cols)
    y = _grid(c, LEFT, split, labour_body_top, LABOUR_ROWS, [w for _, w in labour_cols[:-1]])
    labour_amount_x = split - labour_cols[-1][1]
    labour_bottom = _total_row(c, LEFT, split, labour_amount_x, y, "TOTAL LABOUR")

    col_x = [LEFT]
    for _, width in labour_cols:
        col_x.append(col_x[-1] + width)
    for i, row in enumerate(labour):
        base = labour_body_top - i * ROW_H - 12
        _text(c, (col_x[0] + col_x[1]) / 2, base, row.get("rt", ""), size=8.5, align="center")
        _text(c, (col_x[1] + col_x[2]) / 2, base, row.get("ot", ""), size=8.5, align="center")
        _text(c, col_x[2] + 3, base, _fit(row.get("name", ""), labour_cols[2][1] - 6, 8.5), size=8.5)
        _text(c, col_x[4] - 3, base, row.get("rate", ""), size=8.5, align="right")
        _text(c, col_x[5] - 3, base, row.get("amount", ""), size=8.5, align="right")
    if is_last and filled.get("labour_total"):
        _text(c, split - 3, labour_bottom + 5.5, filled["labour_total"], size=8.5, bold=True, align="right")

    # ── Material / equipment / totals (right) ────────────────────────────
    gap = 8
    rx1 = split + gap
    amount_w = 72
    rx_amount = RIGHT - amount_w
    item_cols = [rx_amount - rx1]

    y = _header_row(c, rx1, RIGHT, table_top, [("MATERIAL", rx_amount - rx1), ("AMOUNT", amount_w)])
    y = _grid(c, rx1, RIGHT, y, 5, item_cols)
    y = _total_row(c, rx1, RIGHT, rx_amount, y, "TOTAL MATERIAL")
    y -= gap
    equipment_body_top = _header_row(c, rx1, RIGHT, y, [("EQUIPMENT", rx_amount - rx1), ("AMOUNT", amount_w)])
    y = _grid(c, rx1, RIGHT, equipment_body_top, EQUIPMENT_ROWS, item_cols)
    for i, row in enumerate(equipment):
        base = equipment_body_top - i * ROW_H - 12
        detail = " ".join(part for part in (row.get("quantity", ""), row.get("uom", "")) if part)
        if row.get("rate"):
            detail = f"{detail} @ {row['rate']}".strip()
        label = f"{row.get('name', '')} ({detail})" if detail else row.get("name", "")
        _text(c, rx1 + 3, base, _fit(label, rx_amount - rx1 - 6, 7.5), size=7.5)
        _text(c, RIGHT - 3, base, row.get("amount", ""), size=8.5, align="right")
    y -= gap
    totals = {
        "TOTAL EQUIPMENT": filled.get("equipment_total", ""),
        "TOTAL LABOUR": filled.get("labour_total", ""),
        "SUBTOTAL": filled.get("subtotal", ""),
    }
    for label in ("TOTAL EQUIPMENT", "TOTAL MATERIAL", "TOTAL LABOUR", "SUBTOTAL", "GST", "TOTAL"):
        y = _total_row(c, rx1, RIGHT, rx_amount, y, label)
        if is_last and totals.get(label):
            _text(c, RIGHT - 3, y + 5.5, totals[label], size=8.5, bold=True, align="right")
    totals_bottom = y

    # ── Approval stamp (left) & signature (right) ────────────────────────
    stamp_top = labour_bottom - 22
    _text(c, LEFT, stamp_top + 6, "Approval Stamp", size=10, bold=True)
    stamp_bottom = 48
    c.rect(LEFT, stamp_bottom, split - LEFT, stamp_top - stamp_bottom)

    sig_y = max(stamp_bottom + 46, min(totals_bottom - 40, 110))
    c.setLineWidth(0.5)
    for label, line_y in (("SIGNATURE", sig_y), ("DATE", stamp_bottom + 6)):
        _text(c, rx1, line_y + 2, label, size=8, bold=True)
        c.line(rx1 + (58 if label == "SIGNATURE" else 30), line_y, RIGHT, line_y)
