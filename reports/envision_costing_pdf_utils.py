"""
Envision GEO — Costing LEM PDF Generator
Same header/footer as the Field Ticket LEM.

Table columns:
  Name/Asset | Date | Work Type | Task | Description | Hrs/Units | Rate | Total

Layout:
  - Labour rows grouped by job_title/Work Type ONLY (all employees doing that
    Work Type share one group) → one subtotal row per Work Type, showing both
    total hours and total cost across every employee in that group
  - Asset rows → Asset Total row
  - Grand Total row

Expected `data` dict shape:
{
    "lem_number":       "000001",
    "lem_date":         "May 20 – May 21, 2026",
    "company_address":  ["1201 5 St SW", "Suite 203", "Calgary AB T2R 2Y6"],
    "project_name":     "Highway 2 Overpass",
    "task_name":        "Bridge Deck Survey",   # "" if no task
    "job_number":       "260142",
    "client":           "Kiewit Construction",
    "pm_name":          "Tyson Bancroft",
    "pm_contact":       "accounting@envisiongeo.ca",
    "pm_phone":         "403-902-1221",
    "labour_groups": [
        {
            "job_title": "PC",
            "entries": [
                {"employee": "Tyson Bancroft", "date": "May 20, 2026", "task": "Bridge Deck Survey",
                 "description": "Orientation", "hours": "4", "rate": "150", "total": "600"},
                ...
            ],
            "hours_total": "10",
            "subtotal": "750",
        },
        ...
    ],
    "asset_rows": [
        {"name": "UAV", "date": "May 21, 2026",
         "hours_units": "1", "rate": "250", "total": "250"},
        ...
    ],
    "asset_total":  "250",
    "grand_total":  "1,200",
    "client_rep":   "",
    "sign":         False,
    "sign_name":    "",
    "sign_date":    "",
}
"""

import io
from functools import partial

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

# Reuse header builder + shared helpers from the Field Ticket utils
from .envision_pdf_utils import (
    BORDER_CLR,
    CONTENT_W,
    HEADER_FG,
    HEADER_GRAY,
    L_MARGIN, R_MARGIN, T_MARGIN, B_MARGIN,
    PAGE_W,
    _build_header,
    _draw_page_number,
    _style,
    p,
)

# ── Extra colours for the costing table ──────────────────────────────────────
SUBTOTAL_BG = colors.HexColor("#DCE6F1")   # light blue — per-group subtotals
GRAND_BG    = colors.HexColor("#1F3864")   # dark navy  — grand total row
GRAND_FG    = colors.white

# ── Column widths (must sum to CONTENT_W = 7.0 in) ───────────────────────────
from reportlab.lib.units import inch

COL_WIDTHS = [
    1.35 * inch,   # Name / Asset
    0.75 * inch,   # Date
    0.80 * inch,   # Work Type
    0.80 * inch,   # Task
    1.20 * inch,   # Description
    0.60 * inch,   # Hrs / Units
    0.75 * inch,   # Rate
    0.75 * inch,   # Total
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(v):
    """Return a clean number string — int if whole, otherwise 2dp."""
    try:
        f = float(str(v).replace(",", ""))
        return str(int(f)) if f == int(f) else f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(v or "")


def _pc(text, bold=False, align=TA_LEFT, color=colors.black):
    return p(text, size=9, bold=bold, align=align, color=color)


# ── Table builder ─────────────────────────────────────────────────────────────

def _build_costing_table(data):
    """Build the full costing table as a single ReportLab Table (repeatRows=1)."""

    # ── Header row ────────────────────────────────────────────────────────────
    rows = [[
        _pc("NAME / ASSET",  bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("DATE",          bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("WORK TYPE",     bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("TASK",          bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("DESCRIPTION",   bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("HRS / UNITS",   bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("RATE",          bold=True, align=TA_CENTER, color=HEADER_FG),
        _pc("TOTAL",         bold=True, align=TA_CENTER, color=HEADER_FG),
    ]]

    style_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), HEADER_GRAY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), HEADER_FG),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER_CLR),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, BORDER_CLR),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]

    ri = 1  # current row index (0 = header)

    # ── Labour groups (one per Work Type, all employees combined) ─────────────
    for group in data.get("labour_groups", []):
        job_title = group.get("job_title", "")

        for entry in group.get("entries", []):
            rows.append([
                _pc(entry.get("employee", "")),
                _pc(entry.get("date", ""),        align=TA_CENTER),
                _pc(job_title,                    align=TA_CENTER),
                _pc(entry.get("task", ""),         align=TA_CENTER),
                _pc(entry.get("description", "")),
                _pc(_fmt(entry.get("hours", "")), align=TA_CENTER),
                _pc(_fmt(entry.get("rate", "")),  align=TA_RIGHT),
                _pc(_fmt(entry.get("total", "")), align=TA_RIGHT),
            ])
            ri += 1

        # Subtotal row — one per Work Type, totalling hours AND cost across
        # every employee in the group (not per user).
        rows.append([
            _pc(""), _pc(""), _pc(""), _pc(""), _pc(""),
            _pc(_fmt(group.get("hours_total", "0")), bold=True, align=TA_CENTER),
            _pc("{} Total".format(job_title), bold=True, align=TA_RIGHT),
            _pc(_fmt(group.get("subtotal", "0")), bold=True, align=TA_RIGHT),
        ])
        style_cmds.append(("BACKGROUND", (0, ri), (-1, ri), SUBTOTAL_BG))
        ri += 1

    # ── Asset rows ────────────────────────────────────────────────────────────
    for asset in data.get("asset_rows", []):
        rows.append([
            _pc(asset.get("name", "")),
            _pc(asset.get("date", ""),                align=TA_CENTER),
            _pc(""),                                   # Work Type — empty for assets
            _pc(""),                                   # Task — empty for assets
            _pc(""),                                   # Description — empty
            _pc(_fmt(asset.get("hours_units", "")),   align=TA_CENTER),
            _pc(_fmt(asset.get("rate", "")),          align=TA_RIGHT),
            _pc(_fmt(asset.get("total", "")),         align=TA_RIGHT),
        ])
        ri += 1

    # Asset total (only if there are assets)
    if data.get("asset_rows"):
        rows.append([
            _pc(""), _pc(""), _pc(""), _pc(""), _pc(""), _pc(""),
            _pc("Asset Total", bold=True, align=TA_RIGHT),
            _pc(_fmt(data.get("asset_total", "0")), bold=True, align=TA_RIGHT),
        ])
        style_cmds.append(("BACKGROUND", (0, ri), (-1, ri), SUBTOTAL_BG))
        ri += 1

    # ── Grand total ───────────────────────────────────────────────────────────
    rows.append([
        _pc(""), _pc(""), _pc(""), _pc(""), _pc(""), _pc(""),
        _pc("Total", bold=True, align=TA_RIGHT, color=GRAND_FG),
        _pc(_fmt(data.get("grand_total", "0")), bold=True, align=TA_RIGHT, color=GRAND_FG),
    ])
    style_cmds.append(("BACKGROUND", (0, ri), (-1, ri), GRAND_BG))
    style_cmds.append(("TEXTCOLOR",  (0, ri), (-1, ri), GRAND_FG))

    tbl = Table(rows, colWidths=COL_WIDTHS, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


# ── Footer (signature block) ──────────────────────────────────────────────────

def _build_costing_footer(data):
    """Client rep + signature + thank-you — appears once after the table."""
    elements = [Spacer(1, 14)]

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

    elements.append(Spacer(1, 10))
    elements.append(p("THANK YOU FOR YOUR BUSINESS!", size=11, bold=True, align=TA_CENTER))
    return elements


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_envision_costing_pdf(data: dict) -> io.BytesIO:
    """
    Build the Envision Costing LEM PDF from `data` and return a BytesIO buffer.

    Page 1 : full header + costing table start + footer
    Page 2+: costing table continues (column headers repeat via repeatRows=1);
             footer (signature) appears once after the last table row.
    """
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=(PAGE_W, __import__("reportlab").lib.pagesizes.letter[1]),
        leftMargin=L_MARGIN,
        rightMargin=R_MARGIN,
        topMargin=T_MARGIN,
        bottomMargin=B_MARGIN,
    )

    story = []
    story.extend(_build_header(data))
    story.append(Spacer(1, 6))
    story.append(_build_costing_table(data))
    story.extend(_build_costing_footer(data))

    # Page-number callback — we don't know total pages upfront so we use
    # a single-pass build and write "Page N" without "of X".
    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawCentredString(
            PAGE_W / 2.0,
            B_MARGIN / 2.0,
            "Page {}".format(canvas.getPageNumber()),
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buffer.seek(0)
    return buffer
