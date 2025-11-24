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

def calculate_rt_ot_and_cost(approval_items):
    """
    Calculate RT, OT, and cost grouped by (date, project).
    approval_items = queryset of TimeEntryApprovalItem with related time_entry & project
    """

    # Group: (date, project_id) -> total hours
    daily_project_hours = defaultdict(Decimal)  # Decimal for accurate sums

    # Also track hourly rate and project info
    project_rates = {}

    for item in approval_items:
        entry = item.time_entry
        date = entry.start_time.date()
        project = entry.project

        # total hours for that day for that project
        hours = Decimal(entry.duration) / Decimal(60)

        key = (date, project.id)
        daily_project_hours[key] += hours

        project_rates[key] = {
            "hourly_rate": entry.hourly_rate,
            "billable": entry.billable,
            "project": project
        }

    total_rt = Decimal("0.00")
    total_ot = Decimal("0.00")
    total_cost = Decimal("0.00")

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
        total_cost += (rt_cost + ot_cost)

    return {
        "rt_hours": float(total_rt),
        "ot_hours": float(total_ot),
        "total_hours": float(total_rt + total_ot),
        "total_cost": float(total_cost)
    }