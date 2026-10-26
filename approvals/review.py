"""
Reviewing a timesheet week entry by entry.

Every TimeEntryApprovalItem has its own status: pending until the approver decides, then approved (the entry is
locked) or rejected with a reason (the entry is unlocked so the employee can fix it). Editing a rejected entry
resubmits it: the item goes back to pending and keeps the reason, so the reviewer sees what was asked for. The week
(TimeEntryApproval) is approved once every entry is; it is rejected when the approver rejects the whole week.
"""

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from core.utils.envision_time import utc_to_envision_local

from .models import TimeEntryApprovalItem
from .utils import calculate_rt_ot_and_cost

PENDING = TimeEntryApprovalItem.STATUS_PENDING
APPROVED = TimeEntryApprovalItem.STATUS_APPROVED
REJECTED = TimeEntryApprovalItem.STATUS_REJECTED


def decide_item(item, status, user, reason=""):
    """Record the approver's decision on one entry and lock (approved) or unlock (rejected) it."""
    item.status = status
    item.approved = status != REJECTED
    if status == REJECTED:
        item.comments = reason
    item.decided_by = user
    item.decided_at = timezone.now()
    item.save(update_fields=["status", "approved", "comments", "decided_by", "decided_at"])

    entry = item.time_entry
    locked = status == APPROVED
    if entry.is_locked != locked:
        entry.is_locked = locked
        entry.save(update_fields=["is_locked"])


def refresh_week_status(approval, user=None):
    """Approved once every entry is approved; otherwise back to submitted (unless the whole week was rejected)."""
    statuses = set(approval.items.filter(is_deleted=False).values_list("status", flat=True))
    if statuses and statuses == {APPROVED}:
        approval.status = "approved"
        approval.reviewed_by = user or approval.reviewed_by
    elif approval.status == "approved":
        approval.status = "submitted"
    approval.save(update_fields=["status", "reviewed_by"])


def resubmit_entry(time_entry):
    """The employee changed a rejected entry: it goes back to the approver, the rejection reason kept."""
    items = TimeEntryApprovalItem.objects.filter(time_entry=time_entry, status=REJECTED).select_related("approval")
    for item in items:
        item.status = PENDING
        item.approved = True
        item.resubmitted_at = timezone.now()
        item.save(update_fields=["status", "approved", "resubmitted_at"])
        approval = item.approval
        if approval.status == "rejected":
            approval.status = "submitted"
            approval.save(update_fields=["status"])


def _money(value):
    return round(float(value or 0), 2)


def _person(user):
    return (user.get_full_name() or user.email) if user else ""


def _usage_lines(entry, billable):
    lines = []
    for usage in entry.asset_usages.all():
        asset = usage.asset
        hourly = asset.charge_type == "hourly"
        quantity = usage.quantity_used
        if quantity is None and hourly:
            quantity = Decimal(entry.duration) / 60
        lines.append({
            "name": asset.name,
            "kind": "equipment" if asset.is_equipment else "rate",
            "unit": "h" if hourly else "unit",
            "quantity": _money(quantity),
            "rate": _money(asset.hourly_rate if hourly else asset.quantity_rate) if billable else None,
            "amount": _money(usage.cost) if billable else None,
        })
    return lines


def _entry_detail(item, ticket_numbers):
    entry = item.time_entry
    start = utc_to_envision_local(entry.start_time)
    end = utc_to_envision_local(entry.end_time) if entry.end_time else None
    billable = entry.is_billable_to_client
    project = entry.project
    lines = _usage_lines(entry, billable)
    return {
        "item_id": str(item.id),
        "status": item.status,
        "reason": item.comments,
        "decided_by": _person(item.decided_by),
        "decided_at": item.decided_at,
        "resubmitted_at": item.resubmitted_at,
        "time_entry_id": str(entry.id),
        "date": start.date().isoformat(),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M") if end else "",
        "hours": _money(Decimal(entry.duration) / 60),
        "project": project.name if project else "",
        "client": project.client.name if project and project.client else "",
        "task": entry.task.name if entry.task else "",
        "job_title": entry.job_title.name if entry.job_title else "",
        "hourly_rate": _money(entry.hourly_rate) if billable and entry.hourly_rate is not None else None,
        "labour_cost": _money(entry.cost) if billable else None,
        "description": entry.description,
        "billable": billable,
        "non_billable_project": bool(project and not project.billable),
        "meals": entry.meals,
        "hotels": entry.hotels,
        "source": ticket_numbers.get(entry.id) or "",
        "lines": lines,
        "lines_total": _money(sum(line["amount"] or 0 for line in lines)) if billable else None,
    }


def week_detail(approval):
    """Every entry of the week with what the employee entered, grouped by day, plus day and week totals."""
    items = list(
        approval.items.filter(is_deleted=False, time_entry__is_deleted=False)
        .select_related(
            "decided_by", "time_entry__project__client", "time_entry__task", "time_entry__job_title",
        )
        .prefetch_related("time_entry__asset_usages__asset", "time_entry__lem_reports")
        .order_by("time_entry__start_time")
    )
    ticket_numbers = {}
    for item in items:
        tickets = [lem for lem in item.time_entry.lem_reports.all() if not lem.is_deleted]
        if tickets:
            ticket_numbers[item.time_entry_id] = tickets[0].ticket_number

    summary = calculate_rt_ot_and_cost(items, workspace=approval.workspace)
    day_hours = defaultdict(lambda: {"total": Decimal(0), "rt": Decimal(0), "ot": Decimal(0)})
    for (day, _project), bucket in summary["breakdown"].items():
        for key in ("total", "rt", "ot"):
            day_hours[str(day)][key] += bucket[key]

    entries = [_entry_detail(item, ticket_numbers) for item in items]
    days = defaultdict(list)
    for entry in entries:
        days[entry["date"]].append(entry)

    day_rows = []
    for day, day_entries in sorted(days.items()):
        hours = day_hours.get(day)
        day_rows.append({
            "date": day,
            "hours": _money(sum(e["hours"] for e in day_entries)),
            "rt_hours": _money(hours["rt"]) if hours else None,
            "ot_hours": _money(hours["ot"]) if hours else None,
            "labour_cost": _money(sum(e["labour_cost"] or 0 for e in day_entries)),
            "lines_total": _money(sum(e["lines_total"] or 0 for e in day_entries)),
            "non_billable": all(not e["billable"] for e in day_entries),
            "entries": day_entries,
        })

    counts = {PENDING: 0, APPROVED: 0, REJECTED: 0}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    lines_total = sum(e["lines_total"] or 0 for e in entries)
    return {
        "summary": {
            "total_hours": summary["total_hours"],
            "regular_hours": summary["rt_hours"],
            "overtime_hours": summary["ot_hours"],
            "total_cost": summary["total_cost"],
            "labour_cost": summary["total_cost"],
            "lines_total": _money(lines_total),
            "grand_total": _money(Decimal(str(summary["total_cost"])) + Decimal(str(lines_total))),
            "non_billable_hours": _money(sum(e["hours"] for e in entries if not e["billable"])),
        },
        "item_counts": counts,
        "days": day_rows,
    }
