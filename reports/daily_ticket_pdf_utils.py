"""
Blank Daily Ticket (paper LEM) PDF for Bigstone Oilfield Services & Supplies Ltd.

A copy of the pre-printed ticket book page, meant to be printed and filled in by hand in the field.
Only the ticket number and date are filled in (both assigned when the ticket is downloaded); every
other field, the approval stamp and the signature are left blank.
"""

import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
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


def _text(c, x, y, value, size=8, bold=False, align="left", color=INK):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    if align == "center":
        c.drawCentredString(x, y, value)
    elif align == "right":
        c.drawRightString(x, y, value)
    else:
        c.drawString(x, y, value)


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


def generate_daily_ticket_pdf(ticket_number: str, ticket_date: str) -> io.BytesIO:
    """
    ticket_number: e.g. "26-00001" (printed as "26 - 00001")
    ticket_date:   already formatted for printing, e.g. "Oct 23, 2026"
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"Daily Ticket {ticket_number}")
    c.setAuthor("Bigstone Oilfield Services & Supplies Ltd.")
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
    yy, _, seq = ticket_number.partition("-")
    _text(c, box_x2 - 12, box_top - number_h + 11, f"{yy} - {seq}", size=18, bold=True, align="right", color=NAVY)

    fields = [
        ("DATE", ticket_date),
        ("JOB LOCATION", ""),
        ("EMPLOYEE", ""),
        ("CONSULTANT'S NAME & EMAIL", ""),
    ]
    y = box_top - number_h
    for label, value in fields:
        c.rect(box_x1, y - field_h, box_x2 - box_x1, field_h)
        _text(c, box_x1 + 5, y - 9, label, size=6.5, bold=True)
        if value:
            _text(c, box_x1 + 70, y - field_h + 7, value, size=11, bold=True)
        y -= field_h
    box_bottom = y

    # ── TO: lines ────────────────────────────────────────────────────────
    to_y = contact_bottom - 8
    _text(c, LEFT, to_y + 2, "TO:", size=9, bold=True)
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
    c.setLineWidth(0.4)
    c.setStrokeColor(FAINT)
    for i in range(1, desc_lines):
        line_y = desc_top - HEADER_H - i * desc_line_h
        c.line(LEFT + 4, line_y, RIGHT - 4, line_y)
    c.setStrokeColor(INK)
    c.setLineWidth(LINE_W)

    # ── Labour (left) ────────────────────────────────────────────────────
    table_top = desc_bottom - 8
    split = 312
    labour_cols = [("RT", 28), ("OT", 28), ("EMPLOYEE", 112), ("PRICE", 50), ("AMOUNT", split - LEFT - 218)]
    y = _header_row(c, LEFT, split, table_top, labour_cols)
    y = _grid(c, LEFT, split, y, 14, [w for _, w in labour_cols[:-1]])
    labour_amount_x = split - labour_cols[-1][1]
    labour_bottom = _total_row(c, LEFT, split, labour_amount_x, y, "TOTAL LABOUR")

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
    y = _header_row(c, rx1, RIGHT, y, [("EQUIPMENT", rx_amount - rx1), ("AMOUNT", amount_w)])
    y = _grid(c, rx1, RIGHT, y, 4, item_cols)
    y -= gap
    for label in ("TOTAL EQUIPMENT", "TOTAL MATERIAL", "TOTAL LABOUR", "SUBTOTAL", "GST", "TOTAL"):
        y = _total_row(c, rx1, RIGHT, rx_amount, y, label)
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

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
