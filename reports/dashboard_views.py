"""
Dashboard reporting endpoints — GET /api/reports/dashboard/*

Admin widgets (hours-by-project, field-tickets, asset-costs) are filterable
by project / user / date range. Non-admin widgets are always scoped to the
requesting user.

"Field ticket" is not a separate model — it's an `LEMReport` row whose
`lem_number` starts with "FT" (the whole-day "FT-" series from
EnvisionLEMReportView, and the per-submission "FTF-" series from
EnvisionFieldTicketLEMFromPayloadView both represent an actual field ticket;
"CT-" is a Costing Ticket and plain "LEM-" is the generic/non-Envision path,
neither of which counts here). Each LEMReport's `time_entries` M2M is exactly
the entries that ticket covers, so hours/cost are computed from that link
rather than re-deriving it from project/date alone.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Sum
from django.utils import timezone
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.utils import get_week_bounds
from core.utils.workspace_utils import get_user_workspace_ids
from organization_asset.models import AssetUsage
from projects.models import Project, UserProjectRole
from time_entries.models import TimeEntry
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser

from .models import LEMReport

FIELD_TICKET_LEM_PREFIX = "FT"  # matches both the "FT-" and "FTF-" series


def _resolve_workspace_ids(request):
    user = request.user
    if user.is_superuser:
        workspace_id = request.GET.get("workspace")
        if not workspace_id:
            raise ValidationError({"workspace": "Workspace parameter is required for superusers."})
        return [workspace_id]
    return list(get_user_workspace_ids(user))


def _resolve_date_range(request):
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    if date_from and date_to:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").date()
            end = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError({"date_from": "Use YYYY-MM-DD."})
        return start, end
    return get_week_bounds(timezone.now().date())


def _month_bounds(today):
    start = today.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def _minutes_to_hours(minutes):
    return round((minutes or 0) / 60, 1)


class DashboardSummaryView(APIView):
    """GET /api/reports/dashboard/summary/ — role-aware KPI + activity snapshot, no filters."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        workspace_ids = _resolve_workspace_ids(request)
        start, end = get_week_bounds(timezone.now().date())

        is_admin = user.is_superuser or WorkspaceMember.objects.filter(
            user=user, workspace_id__in=workspace_ids, is_deleted=False, role__in=["admin", "manager"],
        ).exists()

        if not is_admin:
            return Response(self._member_summary(user, start, end))

        week_qs = TimeEntry.objects.filter(
            workspace_id__in=workspace_ids, start_time__date__range=[start, end], is_deleted=False,
        )
        week_totals = week_qs.aggregate(total_minutes=Sum("duration"), total_entries=Count("id"))
        total_minutes = week_totals["total_minutes"] or 0
        total_entries = week_totals["total_entries"] or 0
        active_projects = Project.objects.filter(
            workspace_id__in=workspace_ids, is_active=True, is_deleted=False,
        ).count()

        most_active_rows = (
            week_qs.values("user_id", "user__first_name", "user__last_name")
            .annotate(minutes=Sum("duration"))
            .order_by("-minutes")[:5]
        )
        most_active = [
            {
                "user_id": str(r["user_id"]),
                "name": f"{r['user__first_name']} {r['user__last_name']}".strip(),
                "hours": _minutes_to_hours(r["minutes"]),
            }
            for r in most_active_rows
        ]

        fourteen_days_ago = timezone.now() - timedelta(days=14)
        active_recently = set(
            TimeEntry.objects.filter(
                workspace_id__in=workspace_ids, start_time__gte=fourteen_days_ago, is_deleted=False,
            ).values_list("user_id", flat=True).distinct()
        )
        members = WorkspaceMember.objects.filter(
            workspace_id__in=workspace_ids, is_deleted=False, user__is_deleted=False, user__is_active=True,
        ).select_related("user")
        inactive_members = [m for m in members if m.user_id not in active_recently]

        # One aggregated query for every inactive member's last entry, instead
        # of one query per member.
        last_entry_by_user = {
            row["user_id"]: row["last"]
            for row in TimeEntry.objects.filter(
                user_id__in=[m.user_id for m in inactive_members],
                workspace_id__in=workspace_ids,
                is_deleted=False,
            ).values("user_id").annotate(last=Max("start_time"))
        }

        inactive_users = []
        for m in inactive_members:
            last_start = last_entry_by_user.get(m.user_id)
            if last_start:
                days = (timezone.now().date() - last_start.date()).days
                status = f"No entries in {days} days"
            else:
                status = "Never logged time"
            inactive_users.append({"user_id": str(m.user_id), "name": m.user.get_full_name(), "status": status})

        return Response({
            "is_admin": True,
            "total_hours_week": _minutes_to_hours(total_minutes),
            "total_entries_week": total_entries,
            "active_projects": active_projects,
            "most_active": most_active,
            "inactive_users": inactive_users,
        })

    def _member_summary(self, user, start, end):
        project_ids = list(
            UserProjectRole.objects.filter(
                user=user, is_deleted=False, project__is_active=True, project__is_deleted=False,
            ).values_list("project_id", flat=True).distinct()
        )
        rows = (
            TimeEntry.objects.filter(
                user=user, project_id__in=project_ids, start_time__date__range=[start, end], is_deleted=False,
            ).values("project_id").annotate(minutes=Sum("duration"))
        )
        hours_by_project = {str(r["project_id"]): _minutes_to_hours(r["minutes"]) for r in rows}
        projects = Project.objects.filter(id__in=project_ids).values("id", "name")
        my_projects = [
            {
                "project_id": str(p["id"]),
                "project_name": p["name"],
                "hours": hours_by_project.get(str(p["id"]), 0),
            }
            for p in projects
        ]
        return {"is_admin": False, "my_projects_week": my_projects}


class DashboardHoursByProjectView(APIView):
    """GET /api/reports/dashboard/hours-by-project/ — admin only, filterable pie-chart data."""

    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]

    def get(self, request):
        workspace_ids = _resolve_workspace_ids(request)
        start, end = _resolve_date_range(request)

        qs = TimeEntry.objects.filter(
            workspace_id__in=workspace_ids, start_time__date__range=[start, end], is_deleted=False,
        ).exclude(project__isnull=True)

        project_id = request.GET.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        user_id = request.GET.get("user")
        if user_id:
            qs = qs.filter(user_id=user_id)

        rows = (
            qs.values("project_id", "project__name")
            .annotate(minutes=Sum("duration"))
            .order_by("-minutes")
        )
        return Response([
            {
                "project_id": str(r["project_id"]),
                "project_name": r["project__name"],
                "hours": _minutes_to_hours(r["minutes"]),
            }
            for r in rows
        ])


class DashboardFieldTicketsView(APIView):
    """GET /api/reports/dashboard/field-tickets/ — admin only, filterable bar-chart data."""

    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]

    def get(self, request):
        workspace_ids = _resolve_workspace_ids(request)
        start, end = _resolve_date_range(request)

        lem_qs = LEMReport.objects.filter(
            project__workspace_id__in=workspace_ids,
            lem_number__startswith=FIELD_TICKET_LEM_PREFIX,
            lem_date__range=[start, end],
            is_deleted=False,
        ).exclude(project__isnull=True).select_related("project").prefetch_related("time_entries__asset_usages")

        project_id = request.GET.get("project")
        if project_id:
            lem_qs = lem_qs.filter(project_id=project_id)
        user_id = request.GET.get("user")
        if user_id:
            lem_qs = lem_qs.filter(time_entries__user_id=user_id).distinct()

        by_project = {}
        for lem in lem_qs:
            entries = list(lem.time_entries.all())
            if user_id:
                entries = [e for e in entries if str(e.user_id) == str(user_id)]
            if not entries:
                continue

            hours = sum((e.duration or 0) for e in entries) / Decimal(60)
            labor_cost = sum((e.cost or Decimal(0)) for e in entries)
            asset_cost = sum(
                (au.cost or Decimal(0)) for e in entries for au in e.asset_usages.all()
            )

            bucket = by_project.setdefault(lem.project_id, {
                "project_id": str(lem.project_id),
                "project_name": lem.project.name,
                "tickets": 0,
                "hours": Decimal(0),
                "cost": Decimal(0),
            })
            bucket["tickets"] += 1
            bucket["hours"] += hours
            bucket["cost"] += labor_cost + asset_cost

        result = sorted(by_project.values(), key=lambda b: b["hours"], reverse=True)
        for r in result:
            r["hours"] = round(float(r["hours"]), 1)
            r["cost"] = round(float(r["cost"]), 2)
        return Response(result)


class DashboardAssetCostsView(APIView):
    """GET /api/reports/dashboard/asset-costs/ — admin only, per-project asset cost breakdown."""

    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]

    def get(self, request):
        workspace_ids = _resolve_workspace_ids(request)
        start, end = _resolve_date_range(request)

        qs = AssetUsage.objects.filter(
            time_entry__workspace_id__in=workspace_ids,
            time_entry__start_time__date__range=[start, end],
            is_deleted=False,
        ).exclude(time_entry__project__isnull=True).select_related("asset", "time_entry__project")

        project_id = request.GET.get("project")
        if project_id:
            qs = qs.filter(time_entry__project_id=project_id)
        user_id = request.GET.get("user")
        if user_id:
            qs = qs.filter(time_entry__user_id=user_id)

        by_project = {}
        for usage in qs:
            project = usage.time_entry.project
            bucket = by_project.setdefault(project.id, {
                "project_id": str(project.id),
                "project_name": project.name,
                "assets": {},
            })
            bucket["assets"].setdefault(usage.asset.name, Decimal(0))
            bucket["assets"][usage.asset.name] += usage.cost or Decimal(0)

        result = []
        for bucket in by_project.values():
            items = [
                {"asset": name, "cost": round(float(cost), 2)}
                for name, cost in sorted(bucket["assets"].items(), key=lambda kv: kv[1], reverse=True)
            ]
            subtotal = sum(bucket["assets"].values())
            result.append({
                "project_id": bucket["project_id"],
                "project_name": bucket["project_name"],
                "items": items,
                "subtotal": round(float(subtotal), 2),
            })
        result.sort(key=lambda r: r["subtotal"], reverse=True)
        return Response(result)


class DashboardMyHoursByProjectView(APIView):
    """GET /api/reports/dashboard/my-hours-by-project/?period=week|month — non-admin, own hours per project."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        period = request.GET.get("period", "week")
        today = timezone.now().date()
        if period == "month":
            start, end = _month_bounds(today)
        else:
            start, end = get_week_bounds(today)

        rows = (
            TimeEntry.objects.filter(
                user=request.user, start_time__date__range=[start, end], is_deleted=False,
            ).exclude(project__isnull=True)
            .values("project_id", "project__name")
            .annotate(minutes=Sum("duration"))
            .order_by("-minutes")
        )
        return Response([
            {
                "project_id": str(r["project_id"]),
                "project_name": r["project__name"],
                "hours": _minutes_to_hours(r["minutes"]),
            }
            for r in rows
        ])


class DashboardMyHoursByTaskView(APIView):
    """GET /api/reports/dashboard/my-hours-by-task/?project=<id> — non-admin, own hours by task this month."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        project_id = request.GET.get("project")
        if not project_id:
            raise ValidationError({"project": "This field is required."})

        today = timezone.now().date()
        start, end = _month_bounds(today)

        rows = (
            TimeEntry.objects.filter(
                user=request.user, project_id=project_id, start_time__date__range=[start, end], is_deleted=False,
            ).exclude(task__isnull=True)
            .values("task_id", "task__name")
            .annotate(minutes=Sum("duration"))
            .order_by("-minutes")
        )
        return Response([
            {
                "task_id": str(r["task_id"]),
                "task_name": r["task__name"],
                "hours": _minutes_to_hours(r["minutes"]),
            }
            for r in rows
        ])
