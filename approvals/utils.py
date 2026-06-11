from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from workspaces.models import WorkspaceMember

def can_approve(request_user, target_user, workspace):
    """
    Final Rules:
     - Superuser can approve anyone.
     - If employee has manager → manager, admin, or superuser can approve.
     - If no manager → admin or superuser can approve.
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
        # // Manager or Admin of workspace can approve
        if employee.manager == request_user or approver.role == "admin":
            return True
        return False

    # CASE 2: Employee has NO manager → Only admin can approve
    return approver.role == "admin"

def get_week_bounds(date):
    """Return start (Sunday) and end (Saturday) of the week for given date."""
    # weekday(): Monday=0 ... Sunday=6
    days_since_sunday = (date.weekday() + 1) % 7
    start_of_week = date - timedelta(days=days_since_sunday)
    end_of_week = start_of_week + timedelta(days=6)
    return start_of_week, end_of_week

# ─── EnvisionGeo overtime policy constants ────────────────────────────────
ENVISION_OT_MULTIPLIER   = Decimal("1.5")   # OT paid at hourly_rate * 1.5
ENVISION_WEEKLY_RT_CAP   = Decimal("44")    # max regular hours per week (Mon-Sun)
ENVISION_WEEKDAY_RT_CAP  = Decimal("8")     # Mon-Fri: regular hours per day
ENVISION_SATURDAY_RT_CAP = Decimal("4")     # Saturday: regular hours per day


def _new_breakdown_bucket():
    return {
        "total": Decimal("0.00"),
        "rt": Decimal("0.00"),
        "ot": Decimal("0.00"),
        "rt_cost": Decimal("0.00"),
        "ot_cost": Decimal("0.00"),
        "cost": Decimal("0.00"),
    }


def _calculate_standard(approval_items, breakdown):
    """
    Standard (Stamsh) policy — per-day per-project:
    RT up to project.default_rt_hours, OT beyond at project.ot_multiplier.
    """
    # Group: (date, project_id) -> total hours
    daily_project_hours = defaultdict(Decimal)
    project_rates = {}

    for item in approval_items:
        entry = item.time_entry
        date = entry.start_time.date()
        project = entry.project

        hours = round(Decimal(entry.duration) / Decimal(60), 2)

        key = (date, project.id)
        daily_project_hours[key] += hours

        project_rates[key] = {
            "hourly_rate": entry.hourly_rate,
            "billable": entry.billable,
            "project": project
        }

    total_rt = Decimal("0.00")
    total_ot = Decimal("0.00")
    total_rt_cost = Decimal("0.00")
    total_ot_cost = Decimal("0.00")

    for key, total_hours in daily_project_hours.items():
        proj_data = project_rates[key]
        project = proj_data["project"]
        hourly = proj_data["hourly_rate"]
        billable = proj_data["billable"]

        # RT/OT Calculation
        rt_hours = min(total_hours, Decimal(project.default_rt_hours))
        ot_hours = max(total_hours - Decimal(project.default_rt_hours), Decimal("0.00"))

        # Cost calculation only if billable (Option B)
        if billable:
            rt_cost = hourly * rt_hours
            ot_cost = hourly * Decimal(project.ot_multiplier) * ot_hours
        else:
            rt_cost = Decimal("0.00")
            ot_cost = Decimal("0.00")

        total_rt += rt_hours
        total_ot += ot_hours
        total_rt_cost += rt_cost
        total_ot_cost += ot_cost

        date, _ = key
        bucket = breakdown[(date, project.name)]
        bucket["total"]   += total_hours
        bucket["rt"]      += rt_hours
        bucket["ot"]      += ot_hours
        bucket["rt_cost"] += rt_cost
        bucket["ot_cost"] += ot_cost
        bucket["cost"]    += (rt_cost + ot_cost)

    return total_rt, total_ot, total_rt_cost, total_ot_cost


def _calculate_no_policy(approval_items, breakdown):
    """
    No overtime policy (workspace.overtime_policy is empty):
    every hour is regular time at the entry's hourly rate. Used for
    workspaces that have not configured any overtime rules.
    """
    total_rt = Decimal("0.00")
    total_rt_cost = Decimal("0.00")

    for item in approval_items:
        entry = item.time_entry
        date = entry.start_time.date()
        project = entry.project
        hours = round(Decimal(entry.duration) / Decimal(60), 2)

        if entry.billable:
            rt_cost = Decimal(entry.hourly_rate or 0) * hours
        else:
            rt_cost = Decimal("0.00")

        total_rt += hours
        total_rt_cost += rt_cost

        bucket = breakdown[(date, project.name if project else None)]
        bucket["total"]   += hours
        bucket["rt"]      += hours
        bucket["rt_cost"] += rt_cost
        bucket["cost"]    += rt_cost

    return total_rt, Decimal("0.00"), total_rt_cost, Decimal("0.00")


def _calculate_envision(approval_items, breakdown):
    """
    EnvisionGeo policy:
      - Mon-Fri: first 8 hours/day are regular, excess is OT.
      - Saturday: first 4 hours are regular, excess is OT.
      - Sunday: hours are regular UNTIL the weekly 44 regular hours are
        used up; after that they are OT.
      - Weekly cap: max 44 regular hours per week (Mon-Sun). Any hours
        beyond that are OT even if no daily threshold was crossed.
      - OT is paid at hourly_rate * 1.5.

    Hours are allocated chronologically so the weekly cap consumes
    earlier days first.
    """
    # Sort entries chronologically so daily/weekly caps fill in order
    items = sorted(approval_items, key=lambda i: i.time_entry.start_time)

    daily_rt_used = defaultdict(Decimal)   # date -> regular hours used
    weekly_rt_used = defaultdict(Decimal)  # week_start (Monday) -> regular hours used

    total_rt = Decimal("0.00")
    total_ot = Decimal("0.00")
    total_rt_cost = Decimal("0.00")
    total_ot_cost = Decimal("0.00")

    for item in items:
        entry = item.time_entry
        date = entry.start_time.date()
        project = entry.project
        hours = round(Decimal(entry.duration) / Decimal(60), 2)

        weekday = date.weekday()                       # Mon=0 ... Sun=6
        week_start = date - timedelta(days=weekday)    # Monday of this week

        # Daily regular-hours cap
        if weekday <= 4:                               # Mon-Fri
            daily_cap = ENVISION_WEEKDAY_RT_CAP
        elif weekday == 5:                             # Saturday
            daily_cap = ENVISION_SATURDAY_RT_CAP
        else:                                          # Sunday: no daily cap,
            daily_cap = None                           # weekly 44 cap governs

        weekly_room = ENVISION_WEEKLY_RT_CAP - weekly_rt_used[week_start]
        if daily_cap is not None:
            daily_room = daily_cap - daily_rt_used[date]
            room = min(daily_room, weekly_room)
        else:
            room = weekly_room

        rt = max(min(hours, room), Decimal("0.00"))
        ot = hours - rt

        daily_rt_used[date] += rt
        weekly_rt_used[week_start] += rt

        # Cost calculation only if billable (consistent with standard policy)
        hourly = Decimal(entry.hourly_rate or 0)
        if entry.billable:
            rt_cost = hourly * rt
            ot_cost = hourly * ENVISION_OT_MULTIPLIER * ot
        else:
            rt_cost = Decimal("0.00")
            ot_cost = Decimal("0.00")

        total_rt += rt
        total_ot += ot
        total_rt_cost += rt_cost
        total_ot_cost += ot_cost

        bucket = breakdown[(date, project.name if project else None)]
        bucket["total"]   += hours
        bucket["rt"]      += rt
        bucket["ot"]      += ot
        bucket["rt_cost"] += rt_cost
        bucket["ot_cost"] += ot_cost
        bucket["cost"]    += (rt_cost + ot_cost)

    return total_rt, total_ot, total_rt_cost, total_ot_cost


def calculate_rt_ot_and_cost(approval_items, workspace=None):
    """
    Calculate RT, OT, and cost for a set of TimeEntryApprovalItem.

    Dispatches per workspace overtime policy:
      - 'standard' (Stamsh): per-day per-project RT cap
        (project.default_rt_hours) with OT at project.ot_multiplier.
      - 'envision' (EnvisionGeo): 8h/day Mon-Fri, 4h Sat, Sunday regular
        until weekly cap, 44h weekly regular cap, OT at hourly_rate * 1.5.
      - None / empty (no policy configured): no overtime — every hour
        is regular time at the entry's hourly rate.

    If `workspace` is given, its policy is used for all items. Otherwise
    each item's time_entry.workspace decides (cached per workspace).

    Returns totals plus a per-(date, project) "breakdown" dict.
    """
    standard_items = []
    envision_items = []
    no_policy_items = []
    policy_cache = {}

    for item in approval_items:
        if workspace is not None:
            policy = workspace.overtime_policy
        else:
            ws_id = item.time_entry.workspace_id
            if ws_id not in policy_cache:
                policy_cache[ws_id] = item.time_entry.workspace.overtime_policy
            policy = policy_cache[ws_id]

        if policy == "envision":
            envision_items.append(item)
        elif policy == "standard":
            standard_items.append(item)
        else:
            no_policy_items.append(item)

    breakdown = defaultdict(_new_breakdown_bucket)

    total_rt = Decimal("0.00")
    total_ot = Decimal("0.00")
    total_rt_cost = Decimal("0.00")
    total_ot_cost = Decimal("0.00")

    for calc, items in ((_calculate_standard, standard_items),
                        (_calculate_envision, envision_items),
                        (_calculate_no_policy, no_policy_items)):
        if items:
            rt, ot, rt_cost, ot_cost = calc(items, breakdown)
            total_rt += rt
            total_ot += ot
            total_rt_cost += rt_cost
            total_ot_cost += ot_cost

    return {
        "rt_hours":    round(float(total_rt), 2),
        "ot_hours":    round(float(total_ot), 2),
        "total_hours": round(float(total_rt + total_ot), 2),
        "rt_cost":     round(float(total_rt_cost), 2),
        "ot_cost":     round(float(total_ot_cost), 2),
        "total_cost":  round(float(total_rt_cost + total_ot_cost), 2),
        "breakdown":   dict(breakdown),  # (date, project_name) -> Decimal buckets
    }