"""
Envision GEO — LEM Report PDF Generator
Matches FT_LEM_TEMPLATE.docx layout exactly.
Supports multi-page output: 7 employees per page, full header/footer repeated on each page.

Expected `data` dict shape:
{
    "lem_number": "LEM-FT-001",
    "lem_date": "2026-06-01",
    "company_address": ["1201 5 St SW", "Suite 203", "Calgary AB T2R 2Y6"],
    "project_name": "Highway 2 Overpass",
    "job_number": "260142",
    "client": "Kiewit Construction",
    "pm_name": "Tyson Bancroft",
    "pm_contact": "accounting@envisiongeo.ca",
    "pm_phone": "403-902-1221",
    "labour_rows": [
        {"name": "Tyson Bancroft", "role": "PC", "hours": "8.0", "meals": "1", "hotel": "0"},
        ...
    ],
    "work_description": "Merged paragraph of all time entry descriptions.",
    "equipment_rows": [
        {"item": "OPFC", "hours": "8.00", "days": "", "units": "", "rate": "$150.00", "cost": "$1,200.00"},
        ...
    ],
    "total_cost": "$1,450.00",
    "client_rep": "John Doe",
    "sign": False,
    "sign_name": "",
    "sign_date": "",
}
"""

import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, PageBreak, SimpleDocTemplate, Spacer,
    Table, TableStyle, Paragraph,
)

# ── Layout constants ──────────────────────────────────────────────────────────
HEADER_GRAY = colors.HexColor("#7F7F7F")
HEADER_FG   = colors.white
BORDER_CLR  = colors.HexColor("#BFBFBF")

PAGE_W, PAGE_H = letter
L_MARGIN = R_MARGIN = 0.75 * inch
T_MARGIN = B_MARGIN = 0.75 * inch
CONTENT_W = PAGE_W - L_MARGIN - R_MARGIN   # 7.0 in

# Pagination
EMPLOYEES_PER_PAGE = 7    # safe max per page (verified against layout math)
MIN_EQUIP_ROWS     = 5    # minimum blank equipment rows per page

# Description content row height — fits 3 lines (size=10, leading=13) + 5+5 pt padding
DESC_CONTENT_H = 0.70 * inch

LOGO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "static", "reports", "images", "envisiongeo_logo.png",
)


# ── Style helpers ─────────────────────────────────────────────────────────────

def _style(size=10, bold=False, align=TA_LEFT, color=colors.black, leading=None):
    return ParagraphStyle(
        "_",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or (size + 3),
        textColor=color,
        alignment=align,
    )


def p(text, **kw):
    return Paragraph(str(text), _style(**kw))


def _grid_style(header_rows=1):
    return TableStyle([
        ("BACKGROUND",    (0, 0), (-1, header_rows - 1), HEADER_GRAY),
        ("TEXTCOLOR",     (0, 0), (-1, header_rows - 1), HEADER_FG),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER_CLR),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, BORDER_CLR),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ])


# ── Per-page section builders ─────────────────────────────────────────────────

def _build_header(data):
    """Logo + address/LEM block + project info."""
    elements = []

    # Logo
    if os.path.exists(LOGO_PATH):
        logo = Image(LOGO_PATH, width=2.416 * inch, height=0.59 * inch)
        logo.hAlign = "LEFT"
        elements.append(logo)
    elements.append(Spacer(1, 4))

    # Address + LEM No / Date
    addr_lines = data.get("company_address", [])
    addr_cell  = "<br/>".join(addr_lines)

    lem_meta = Table(
        [
            [p("LEM NO.:", size=10, align=TA_RIGHT),
             p(data.get("lem_number", ""), size=10, align=TA_RIGHT)],
            [p("DATE:",    size=10, align=TA_RIGHT),
             p(data.get("lem_date",   ""), size=10, align=TA_RIGHT)],
        ],
        colWidths=[0.9 * inch, 1.1 * inch],
        style=TableStyle([
            ("ALIGN",         (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING",   (0, 0), (-1, -1), 2),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]),
    )

    header_row = Table(
        [[Paragraph(addr_cell, _style(size=10)), lem_meta]],
        colWidths=[CONTENT_W - 2.0 * inch, 2.0 * inch],
        style=TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]),
    )
    elements.append(header_row)
    elements.append(Spacer(1, 10))

    # Project info
    proj_table = Table(
        [[
            Paragraph(
                "Project Name: {}<br/>Job Number: {}<br/>Client: {}".format(
                    data.get("project_name", ""),
                    data.get("job_number", ""),
                    data.get("client", ""),
                ),
                _style(size=10, leading=16),
            ),
            Paragraph(
                "PM: {}<br/>Contact: {}<br/>Phone No.: {}".format(
                    data.get("pm_name", ""),
                    data.get("pm_contact", ""),
                    data.get("pm_phone", ""),
                ),
                _style(size=10, leading=16),
            ),
        ]],
        colWidths=[CONTENT_W / 2, CONTENT_W / 2],
        style=TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ]),
    )
    elements.append(proj_table)
    elements.append(Spacer(1, 14))

    return elements


def _build_labour_table(labour_chunk):
    """Labour table for one page's worth of employees."""
    labour_cw = [1.769*inch, 1.181*inch, 1.280*inch, 1.279*inch, 1.477*inch]

    labour_data = [[
        p("NAME",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("ROLE",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("HOURS", size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("MEALS", size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("HOTEL", size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
    ]]

    for row in labour_chunk:
        labour_data.append([
            p(row.get("name",  ""), size=10),
            p(row.get("role",  ""), size=10, align=TA_CENTER),
            p(row.get("hours", ""), size=10, align=TA_CENTER),
            p(row.get("meals", ""), size=10, align=TA_CENTER),
            p(row.get("hotel", ""), size=10, align=TA_CENTER),
        ])

    tbl = Table(
        labour_data,
        colWidths=labour_cw,
        rowHeights=[0.28 * inch] * len(labour_data),
    )
    tbl.setStyle(_grid_style())
    return tbl


def _build_footer(data, is_last_page=False):
    """Description + equipment + total (last page only) + client rep / signature + thank-you."""
    elements = []
    elements.append(Spacer(1, 10))

    # Description of work
    desc_data = [
        [p("DESCRIPTION OF WORK PERFORMED", size=10, bold=True, color=HEADER_FG)],
        [p(data.get("work_description", ""), size=10)],
    ]
    desc_tbl = Table(
        desc_data,
        colWidths=[CONTENT_W],
        rowHeights=[0.28 * inch, DESC_CONTENT_H],
    )
    desc_tbl.setStyle(_grid_style())
    elements.append(desc_tbl)
    elements.append(Spacer(1, 10))

    # Equipment / cost table
    equip_cw = [1.530*inch, 1.078*inch, 1.031*inch, 0.985*inch, 1.279*inch, 1.090*inch]

    equip_data = [[
        p("ITEM",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("HOURS", size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("DAYS",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("UNITS", size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("RATE",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
        p("COST",  size=10, bold=True, color=HEADER_FG, align=TA_CENTER),
    ]]

    equip_rows = list(data.get("equipment_rows", []))
    while len(equip_rows) < MIN_EQUIP_ROWS:
        equip_rows.append({})

    for row in equip_rows:
        equip_data.append([
            p(row.get("item",  ""), size=10, align=TA_CENTER),
            p(row.get("hours", ""), size=10, align=TA_CENTER),
            p(row.get("days",  ""), size=10, align=TA_CENTER),
            p(row.get("units", ""), size=10, align=TA_CENTER),
            p(row.get("rate",  ""), size=10, align=TA_CENTER),
            p(row.get("cost",  ""), size=10, align=TA_CENTER),
        ])

    equip_tbl = Table(
        equip_data,
        colWidths=equip_cw,
        rowHeights=[0.28 * inch] * len(equip_data),
    )
    equip_tbl.setStyle(_grid_style())
    elements.append(equip_tbl)

    # Total row — only on the last page (cumulative across all pages)
    if is_last_page:
        total_tbl = Table(
            [[p(""), p("TOTAL", size=10, bold=True, align=TA_RIGHT),
              p(data.get("total_cost", "$0.00"), size=10, bold=True, align=TA_RIGHT)]],
            colWidths=[1.262*inch, 4.798*inch, 0.937*inch],
            rowHeights=[0.28 * inch],
        )
        total_tbl.setStyle(TableStyle([
            ("BOX",           (1, 0), (-1, -1), 0.5, BORDER_CLR),
            ("INNERGRID",     (1, 0), (-1, -1), 0.5, BORDER_CLR),
            ("ALIGN",         (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ]))
        elements.append(total_tbl)
    elements.append(Spacer(1, 14))

    # Client rep / signature
    elements.append(p("Client Rep: {}".format(data.get("client_rep", "")), size=10))
    elements.append(Spacer(1, 4))

    if data.get("sign"):
        elements.append(p(
            "Signed: {}    Date: {}".format(
                data.get("sign_name", ""),
                data.get("sign_date", ""),
            ),
            size=10,
        ))
    else:
        elements.append(p("Signature:", size=10))
        elements.append(Spacer(1, 40))

    # Thank-you footer
    elements.append(Spacer(1, 10))
    elements.append(p("THANK YOU FOR YOUR BUSINESS!", size=11, bold=True, align=TA_CENTER))

    return elements


# ── Main builder ──────────────────────────────────────────────────────────────

def _draw_page_number(canvas, doc, total_pages=1):
    """Draw 'Page X of Y' centred at the bottom of every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#555555"))
    text = "Page {} of {}".format(canvas.getPageNumber(), total_pages)
    canvas.drawCentredString(PAGE_W / 2.0, B_MARGIN / 2.0, text)
    canvas.restoreState()


def generate_envision_lem_pdf(data: dict) -> io.BytesIO:
    """
    Build the Envision LEM PDF from `data` and return a BytesIO buffer.
    Paginates automatically: EMPLOYEES_PER_PAGE employees per page,
    full header and footer repeated on every page.
    """
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=L_MARGIN,
        rightMargin=R_MARGIN,
        topMargin=T_MARGIN,
        bottomMargin=B_MARGIN,
    )

    labour_rows = data.get("labour_rows", [])

    # Chunk employees — always at least one (possibly empty) chunk
    if labour_rows:
        chunks = [
            labour_rows[i: i + EMPLOYEES_PER_PAGE]
            for i in range(0, len(labour_rows), EMPLOYEES_PER_PAGE)
        ]
    else:
        chunks = [[]]   # one empty page

    story = []
    for idx, chunk in enumerate(chunks):
        if idx > 0:
            story.append(PageBreak())

        story.extend(_build_header(data))
        story.append(_build_labour_table(chunk))
        story.extend(_build_footer(data, is_last_page=(idx == len(chunks) - 1)))

    from functools import partial
    on_page = partial(_draw_page_number, total_pages=len(chunks))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)
    return buffer