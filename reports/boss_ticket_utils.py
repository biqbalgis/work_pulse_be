"""
Filling the BOSS paper LEM templates (template 1: Daily Ticket, template 2: Daily Cost Sheet) from a
submitted field ticket.

The field ticket's stored report_data carries "boss_template" (which template the user picked) and "boss"
(the data below), so a revision or a later re-download prints the same template the ticket was issued on.
Fields the field ticket form does not capture (PO#, AFE, CVE codes, materials, GST, ...) stay blank.
"""

from collections import OrderedDict
from datetime import date as date_cls
from decimal import Decimal

from core.utils.pay_rules import DAILY_RT_HOURS, split_entries
from projects.role_utils import DEFAULT_JOB_TITLE_NAME
from .cost_sheet_pdf_utils import generate_cost_sheet_pdf
from .daily_ticket_pdf_utils import generate_daily_ticket_pdf
from .models import DailyTicketDownload

# Tickets issued before the pay rules (snapshots without a split): the first 8 hours were regular time.
RT_HOURS_PER_DAY = DAILY_RT_HOURS

BOSS_TEMPLATES = {
    DailyTicketDownload.DAILY_TICKET: (generate_daily_ticket_pdf, "Daily_Ticket"),
    DailyTicketDownload.COST_SHEET: (generate_cost_sheet_pdf, "Daily_Cost_Sheet"),
}
# Every field ticket prints on a BOSS template; one issued without a choice uses template 1.
DEFAULT_TEMPLATE = DailyTicketDownload.DAILY_TICKET


def _money(value):
    return f"${value:,.2f}"


def _hours(value):
    """8 -> "8", 8.5 -> "8.5", 0 -> ""."""
    if not value:
        return ""
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value:.2f}".rstrip("0")


def _add_rate_usage(rates, usage, entry, split):
    asset = usage.asset
    person = entry.user.get_full_name() or entry.user.email
    hourly = asset.charge_type == "hourly"
    row = rates.setdefault((asset.id, entry.user_id), {
        "name": asset.name,
        "person": person,
        "hourly": hourly,
        "rate": Decimal((asset.hourly_rate if hourly else asset.quantity_rate) or 0),
        "quantity": Decimal("0"),
        "rt": Decimal("0"),
        "ot": Decimal("0"),
        "dt": Decimal("0"),
        "amount": Decimal("0"),
    })
    if usage.quantity_used is not None:
        quantity = Decimal(usage.quantity_used)
    elif hourly:
        quantity = Decimal(entry.duration) / 60
    else:
        quantity = Decimal("0")
    row["quantity"] += quantity
    if hourly and split.total:
        # A crew rate's hours fall in the same regular / overtime / double-time shares as its person's time.
        for key in ("rt", "ot", "dt"):
            row[key] += quantity * getattr(split, key) / split.total
    row["amount"] += Decimal(usage.cost or 0)


def build_boss_ticket_data(entries, project, task, prepared_by=None):
    """Everything the templates print, from a field ticket's TimeEntry rows (with user, job_title and
    asset_usages__asset loaded). Values are already formatted strings so they can be stored as JSON."""
    entries = list(entries)
    pm_info = project.pm_info or {}
    task_name = task.name if task else ""
    # Regular / overtime / double time per entry by the pay rules, from this ticket's own rows only: what the form
    # showed. Other tickets or timesheet time that day never change what this ticket prints.
    splits = split_entries(entries, context=entries)

    # ── Labour: one row per person + job title, RT/OT/DT by the pay rules ───
    labour = OrderedDict()
    for entry in entries:
        key = (entry.user_id, entry.job_title_id)
        row = labour.setdefault(key, {
            "user_id": entry.user_id,
            "name": entry.user.get_full_name() or entry.user.email,
            # The placeholder job title time gets when none was chosen isn't a classification anyone entered.
            "classification": entry.job_title.name if entry.job_title and entry.job_title.name != DEFAULT_JOB_TITLE_NAME else "",
            "hours": Decimal("0"),
            "rt": Decimal("0"),
            "ot": Decimal("0"),
            "dt": Decimal("0"),
            "rate": None,
            "amount": Decimal("0"),
            "meals": False,
            "hotel": False,
        })
        row["hours"] += Decimal(entry.duration) / 60
        for part in ("rt", "ot", "dt"):
            row[part] += getattr(splits[entry.id], part)
        row["amount"] += Decimal(entry.cost or 0)
        if row["rate"] is None and entry.hourly_rate is not None:
            row["rate"] = Decimal(entry.hourly_rate)
        row["meals"] = row["meals"] or entry.meals
        row["hotel"] = row["hotel"] or entry.hotels

    labour_rows = []
    labour_total = Decimal("0")
    for row in labour.values():
        labour_total += row["amount"]
        labour_rows.append({
            "name": row["name"],
            "classification": row["classification"],
            "activity": task_name,
            "uom": "Hour",
            "rate": _money(row["rate"]) if row["rate"] is not None else "",
            "hours": _hours(row["hours"]),
            "rt": _hours(row["rt"]),
            "ot": _hours(row["ot"]),
            "dt": _hours(row["dt"]),
            "subsistence": ", ".join(label for label, on in (("Meals", row["meals"]), ("Hotel", row["hotel"])) if on),
            # No labour rate (the person is billed through their crew rates): no labour amount either.
            "amount": _money(row["amount"]) if row["rate"] is not None else "",
        })

    # ── Equipment: one row per asset ─────────────────────────────────────
    equipment = OrderedDict()
    rates = OrderedDict()  # rate sheet items that are not equipment (e.g. OPFC): one row per item + person
    for entry in entries:
        for usage in entry.asset_usages.all():
            asset = usage.asset
            if not asset.is_equipment:
                _add_rate_usage(rates, usage, entry, splits[entry.id])
                continue
            row = equipment.setdefault(asset.id, {
                "name": asset.name,
                "hourly": asset.charge_type == "hourly",
                "rate": Decimal((asset.hourly_rate if asset.charge_type == "hourly" else asset.quantity_rate) or 0),
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "operators": [],
            })
            if usage.quantity_used is not None:
                row["quantity"] += Decimal(usage.quantity_used)
            elif row["hourly"]:
                row["quantity"] += Decimal(entry.duration) / 60
            row["amount"] += Decimal(usage.cost or 0)
            operator = entry.user.get_full_name() or entry.user.email
            if operator not in row["operators"]:
                row["operators"].append(operator)

    equipment_rows = []
    equipment_total = Decimal("0")
    for row in equipment.values():
        equipment_total += row["amount"]
        equipment_rows.append({
            "name": row["name"],
            "activity": task_name,
            "uom": "Hour" if row["hourly"] else "Qty",
            "rate": _money(row["rate"]) if row["rate"] else "",
            "quantity": _hours(row["quantity"]),
            "operator": ", ".join(row["operators"]),
            "amount": _money(row["amount"]),
        })

    # Rates print in the labour section and count toward the labour total.
    for row in rates.values():
        labour_total += row["amount"]
        quantity = _hours(row["quantity"])
        labour_rows.append({
            "name": f"{row['name']} - {row['person']}" + ("" if row["hourly"] or not quantity else f" x{quantity}"),
            "classification": "Rate",
            "activity": task_name,
            "uom": "Hour" if row["hourly"] else "Qty",
            "rate": _money(row["rate"]) if row["rate"] else "",
            "hours": quantity,
            "rt": _hours(row["rt"]) if row["hourly"] else "",
            "ot": _hours(row["ot"]) if row["hourly"] else "",
            "dt": _hours(row["dt"]) if row["hourly"] else "",
            "subsistence": "",
            "amount": _money(row["amount"]),
        })

    descriptions = []
    for entry in entries:
        text = (entry.description or "").strip()
        if text and text not in descriptions:
            descriptions.append(text)

    consultant = " - ".join(part for part in (pm_info.get("name", ""), pm_info.get("email", "")) if part)
    data = {
        "customer": project.client.name if project.client else "",
        "attention": pm_info.get("name", ""),
        "consultant": consultant,
        "location": project.name,
        "project_number": project.job_code or "",
        "prepared_by": (prepared_by.get_full_name() or prepared_by.email) if prepared_by else "",
        "description": " ".join(descriptions),
        "labour": labour_rows,
        "equipment": equipment_rows,
        "labour_total": _money(labour_total),
        "equipment_total": _money(equipment_total),
        "subtotal": _money(labour_total + equipment_total),
        "non_billable": not project.billable,
    }
    if not project.billable:
        # Non-billable project: hours only. Rates, amounts and totals stay blank; the templates say why.
        for row in labour_rows + equipment_rows:
            row["rate"] = ""
            row["amount"] = ""
        data.update(labour_total="", equipment_total="", subtotal="")
    return data


def _decimal(value):
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip() or "0")
    except ArithmeticError:
        return Decimal("0")


def boss_data_from_snapshot(report_data, project=None):
    """Template data for a ticket issued before the BOSS templates, from the snapshot it was issued with
    (labour_rows / equipment_rows / total_cost), so it prints exactly what was issued. Those tickets carried
    no labour rates or amounts, only hours; the equipment rates and costs are kept."""
    labour_rows = []
    rt_left = {}
    for row in report_data.get("labour_rows") or []:
        hours = _decimal(row.get("hours"))
        remaining = rt_left.setdefault(row.get("name"), RT_HOURS_PER_DAY)
        rt = min(hours, remaining)
        rt_left[row.get("name")] = remaining - rt
        flags = (("Meals", row.get("meals")), ("Hotel", row.get("hotel")))
        labour_rows.append({
            "name": row.get("name", ""),
            "classification": "" if row.get("role") in (None, "-") else row["role"],
            "activity": report_data.get("task_name", ""),
            "uom": "Hour",
            "rate": "",
            "hours": _hours(hours),
            "rt": _hours(rt),
            "ot": _hours(hours - rt),
            "subsistence": ", ".join(label for label, on in flags if str(on) == "1"),
            "amount": "",
        })

    equipment_rows = []
    for row in report_data.get("equipment_rows") or []:
        hourly = bool(row.get("hours"))
        equipment_rows.append({
            "name": row.get("item", ""),
            "activity": report_data.get("task_name", ""),
            "uom": "Hour" if hourly else "Qty",
            "rate": row.get("rate", ""),
            "quantity": _hours(_decimal(row.get("hours") if hourly else row.get("units"))),
            "operator": "",
            "amount": row.get("cost", ""),
        })

    total = _decimal(report_data.get("total_cost"))
    description = report_data.get("work_description") or ""
    non_billable = project is not None and not project.billable
    data = {
        "customer": report_data.get("client", ""),
        "attention": report_data.get("pm_name", ""),
        "consultant": " - ".join(p for p in (report_data.get("pm_name"), report_data.get("pm_contact")) if p),
        "location": report_data.get("project_name", ""),
        "project_number": report_data.get("job_number", ""),
        "prepared_by": report_data.get("sign_name", ""),
        "description": "" if description == "N/A" else description,
        "labour": labour_rows,
        "equipment": equipment_rows,
        "labour_total": "",
        "equipment_total": _money(total),
        "subtotal": _money(total),
        "non_billable": non_billable,
    }
    if non_billable:
        for row in equipment_rows:
            row["rate"] = ""
            row["amount"] = ""
        data.update(equipment_total="", subtotal="")
    return data


def render_boss_ticket(template, ticket_number, ticket_date, data):
    """(pdf_buffer, filename) for a filled BOSS template. ticket_date is "YYYY-MM-DD"; the printed number is
    the ticket date's year + the LEM number, e.g. "000123-R01" on 2026-06-03 -> "26-000123-R01"."""
    generate_pdf, file_prefix = BOSS_TEMPLATES[template]
    try:
        day = date_cls.fromisoformat(ticket_date)
        printed_date = f"{day:%b} {day.day}, {day.year}"
        printed_number = f"{day:%y}-{ticket_number}"
    except (TypeError, ValueError):
        printed_date = ticket_date or ""
        printed_number = ticket_number
    pdf_buffer = generate_pdf(printed_number, printed_date, data)
    return pdf_buffer, f"{file_prefix}_{printed_number}_{ticket_date}.pdf"
