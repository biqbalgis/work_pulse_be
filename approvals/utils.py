from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from core.utils.pay_rules import DT_MULTIPLIER, OT_MULTIPLIER, entry_day, split_entries
from workspaces.models import WorkspaceMember


def can_approve(request_user, target_user, workspace):
    """
    Final Rules:
     - Superuser can approve anyone.
     - If employee has manager -> manager, admin, or superuser can approve.
     - If no manager -> admin or superuser can approve.
    """

    # Superuser bypass
    if request_user.is_superuser:
        return True

    # Must belong to the same workspace
    approver = WorkspaceMember.objects.filter(user=request_user, workspace=workspace).first()
    employee = WorkspaceMember.objects.filter(user=target_user, workspace=workspace).first()

    if not approver or not employee:
        return False

    # CASE 1: Employee has a manager
    if employee.manager:
        if employee.manager == request_user or approver.role == "admin":
            return True
        return False

    # CASE 2: Employee has NO manager -> Only admin can approve
    return approver.role == "admin"


def get_week_bounds(date):
    """Return start (Sunday) and end (Saturday) of the week for given date."""
    # weekday(): Monday=0 ... Sunday=6
    days_since_sunday = (date.weekday() + 1) % 7
    start_of_week = date - timedelta(days=days_since_sunday)
    end_of_week = start_of_week + timedelta(days=6)
    return start_of_week, end_of_week


def _new_breakdown_bucket():
    return {
        "total":   Decimal("0.00"),
        "rt":      Decimal("0.00"),
        "ot":      Decimal("0.00"),
        "dt":      Decimal("0.00"),
        "rt_cost": Decimal("0.00"),
        "ot_cost": Decimal("0.00"),
        "dt_cost": Decimal("0.00"),
        "cost":    Decimal("0.00"),
    }


def calculate_rt_ot_and_cost(approval_items, workspace=None):
    """
    Regular / overtime / double-time hours and labour cost for a set of TimeEntryApprovalItem, by the pay rules
    in core.utils.pay_rules (first 8 h of a day regular, the rest overtime at 1.5x; on a stat holiday the first
    8 h overtime and the rest double time at 2x). Each person's whole day counts, entries outside this set
    included. Labour cost is the entry's hourly rate with those premiums; non-billable time costs nothing here.

    `workspace` is accepted for older callers and no longer changes anything.
    Returns totals plus a per-(date, project_name) breakdown dict.
    """
    items = list(approval_items)
    splits = split_entries([item.time_entry for item in items])
    breakdown = defaultdict(_new_breakdown_bucket)
    totals = defaultdict(Decimal)

    for item in items:
        entry = item.time_entry
        split = splits[entry.id]
        hourly = Decimal(entry.hourly_rate or 0) if entry.is_billable_to_client else Decimal("0")
        costs = {
            "rt_cost": hourly * split.rt,
            "ot_cost": hourly * OT_MULTIPLIER * split.ot,
            "dt_cost": hourly * DT_MULTIPLIER * split.dt,
        }
        bucket = breakdown[(entry_day(entry), entry.project.name if entry.project else None)]
        bucket["total"] += split.total
        for key in ("rt", "ot", "dt"):
            bucket[key] += getattr(split, key)
            totals[key] += getattr(split, key)
        for key, value in costs.items():
            bucket[key] += value
            bucket["cost"] += value
            totals[key] += value

    def number(key):
        return round(float(totals[key]), 2)

    return {
        "rt_hours":    number("rt"),
        "ot_hours":    number("ot"),
        "dt_hours":    number("dt"),
        "total_hours": round(float(totals["rt"] + totals["ot"] + totals["dt"]), 2),
        "rt_cost":     number("rt_cost"),
        "ot_cost":     number("ot_cost"),
        "dt_cost":     number("dt_cost"),
        "total_cost":  round(float(totals["rt_cost"] + totals["ot_cost"] + totals["dt_cost"]), 2),
        "breakdown":   dict(breakdown),
    }
