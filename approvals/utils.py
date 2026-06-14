from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

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


# ── EnvisionGeo overtime policy constants ─────────────────────────────────
ENVISION_OT_MULTIPLIER  = Decimal("1.5")   # OT paid at hourly_rate * 1.5
ENVISION_DAILY_RT_CAP   = Decimal("8")     # max regular hours per day (every day)
ENVISION_WEEKLY_RT_CAP  = Decimal("44")    # max regular hours per week (Sun-Sat)


def _new_breakdown_bucket():
    return {
        "total":   Decimal("0.00"),
        "rt":      Decimal("0.00"),
        "ot":      Decimal("0.00"),
        "rt_cost": Decimal("0.00"),
        "ot_cost": Decimal("0.00"),
        "cost":    Decimal("0.00"),
    }


def _calculate_standard(approval_items, breakdown):
    """
    Standard policy: per-day per-project RT cap (project.default_rt_hours),
    OT beyond that at project.ot_multiplier.
    """
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
            "project": project,
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

        rt_hours = min(total_hours, Decimal(project.default_rt_hours))
        ot_hours = max(total_hours - Decimal(project.default_rt_hours), Decimal("0.00"))

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
    No overtime policy: every hour is regular time at the entry's hourly rate.
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
    EnvisionGeo overtime policy (week = Sunday to Saturday):

      Daily cap:  8 regular hours per day for every day of the week.
                  Any hours beyond 8 on a single day are OT immediately.

      Weekly cap: 44 regular hours per week (Sun-Sat).
                  Once a worker's regular hours for the week reach 44,
                  all further hours that week are OT even if the daily
                  threshold has not been crossed.

    Both caps apply simultaneously and the stricter one wins:
      room = min(daily_room, weekly_room)
      rt   = min(hours_worked, room)
      ot   = hours_worked - rt

    Example (full week, 8 h/day):
      Sun  8 h ->  8 RT,  0 OT  (weekly total  8)
      Mon  8 h ->  8 RT,  0 OT  (weekly total 16)
      Tue  8 h ->  8 RT,  0 OT  (weekly total 24)
      Wed  8 h ->  8 RT,  0 OT  (weekly total 32)
      Thu  8 h ->  8 RT,  0 OT  (weekly total 40)
      Fri  8 h ->  4 RT,  4 OT  (weekly cap 44 reached after 4 h)
      Sat  8 h ->  0 RT,  8 OT  (weekly cap already full)

    If a day is missed the remaining budget rolls into subsequent days
    (still capped at 8 RT/day) until 44 is reached.

    OT is paid at hourly_rate * 1.5.
    """
    # Process entries in chronological order so caps fill correctly
    items = sorted(approval_items, key=lambda i: i.time_entry.start_time)

    daily_rt_used  = defaultdict(Decimal)  # date        -> RT hours used that day
    weekly_rt_used = defaultdict(Decimal)  # week_sunday -> RT hours used that week

    total_rt = Decimal("0.00")
    total_ot = Decimal("0.00")
    total_rt_cost = Decimal("0.00")
    total_ot_cost = Decimal("0.00")

    for item in items:
        entry = item.time_entry
        date = entry.start_time.date()
        project = entry.project
        hours = round(Decimal(entry.duration) / Decimal(60), 2)

        # Week starts on Sunday
        days_since_sunday = (date.weekday() + 1) % 7   # Mon=1 ... Sun=0
        week_sunday = date - timedelta(days=days_since_sunday)

        # How much RT room is left today and this week?
        daily_room  = ENVISION_DAILY_RT_CAP - daily_rt_used[date]
        weekly_room = ENVISION_WEEKLY_RT_CAP - weekly_rt_used[week_sunday]
        room = max(min(daily_room, weekly_room), Decimal("0.00"))

        rt = min(hours, room)
        ot = hours - rt

        daily_rt_used[date]        += rt
        weekly_rt_used[week_sunday] += rt

        hourly = Decimal(entry.hourly_rate or 0)
        if entry.billable:
            rt_cost = hourly * rt
            ot_cost = hourly * ENVISION_OT_MULTIPLIER * ot
        else:
            rt_cost = Decimal("0.00")
            ot_cost = Decimal("0.00")

        total_rt      += rt
        total_ot      += ot
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
      - 'standard'  : per-day per-project RT cap at project.default_rt_hours.
      - 'envision'  : 8 h/day, 44 h/week (Sun-Sat), OT at hourly_rate * 1.5.
      - None / empty: no overtime - every hour is regular time.

    If `workspace` is given its policy is used for all items; otherwise
    each item's time_entry.workspace decides (cached per workspace).

    Returns totals plus a per-(date, project_name) breakdown dict.
    """
    standard_items  = []
    envision_items  = []
    no_policy_items = []
    policy_cache    = {}

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

    total_rt      = Decimal("0.00")
    total_ot      = Decimal("0.00")
    total_rt_cost = Decimal("0.00")
    total_ot_cost = Decimal("0.00")

    for calc, items in (
        (_calculate_standard,  standard_items),
        (_calculate_envision,  envision_items),
        (_calculate_no_policy, no_policy_items),
    ):
        if items:
            rt, ot, rt_cost, ot_cost = calc(items, breakdown)
            total_rt      += rt
            total_ot      += ot
            total_rt_cost += rt_cost
            total_ot_cost += ot_cost

    return {
        "rt_hours":    round(float(total_rt), 2),
        "ot_hours":    round(float(total_ot), 2),
        "total_hours": round(float(total_rt + total_ot), 2),
        "rt_cost":     round(float(total_rt_cost), 2),
        "ot_cost":     round(float(total_ot_cost), 2),
        "total_cost":  round(float(total_rt_cost + total_ot_cost), 2),
        "breakdown":   dict(breakdown),
    }
