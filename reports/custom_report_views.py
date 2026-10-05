"""
POST /api/reports/custom/ — Custom Report builder (Excel download).

Nothing is stored: unlike the Field Ticket / Costing LEM reports this never
creates a LEMReport row, it just streams back a freshly built .xlsx.

Body:
    date_from     (str, required)   YYYY-MM-DD
    date_to       (str, required)   YYYY-MM-DD
    interval      (str, required)   "daily" | "weekly" | "biweekly"
    metrics       (list, required)  any of "total" | "regular" | "overtime"
    group_by      (list, required)  1-2 of "project" | "user" | "task" | "group"
    project_ids   (list, optional)  empty/omitted = all projects (incl. none)
    user_ids      (list, optional)  empty/omitted = all employees
    groups        (list, optional)  user groups (subcontractors | external_envision |
                                    internal_envision); empty/omitted = everyone.
                                    Combined with user_ids as an intersection.
    include_chart (bool, optional)  default false
    chart_type    (str, optional)   "bar" | "line" | "pie" (default "bar")
    workspace     (uuid, optional)  superusers: narrow to one workspace;
                                    others: must be one they're admin/manager in

Access: superusers, and any workspace member whose "custom_report" permission
is switched on (Permissions page). Everyone else gets 403.

GET /api/reports/custom/options/ returns the projects and employees the caller
may pick from (the regular project/user list endpoints are admin-only, so
users who were granted this permission could not populate the pickers there).
"""

from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils.envision_time import envision_day_bounds_utc, utc_to_envision_local
from django.utils import timezone
from projects.models import Project
from time_entries.models import TimeEntry
from user_permissions.models import UserPermission
from users.models import User
from workspaces.models import WorkspaceMember

from .custom_report_utils import (
    CHART_TYPES,
    GROUP_DIMS,
    GROUP_LABELS,
    INTERVALS,
    MAX_DAILY_RANGE_DAYS,
    MAX_RANGE_DAYS,
    METRICS_ORDER,
    METRIC_LABELS,
    aggregate_hours,
    build_buckets,
    generate_custom_report_xlsx,
    week_range_for,
)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_LISTED_NAMES = 8


class _BadRequest(Exception):
    pass


def _parse_date(value, name):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise _BadRequest(f"{name} is required and must be YYYY-MM-DD")


def _as_list(value, name):
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise _BadRequest(f"{name} must be a list")
    return list(value)


def _names_summary(names, empty_text):
    names = sorted(set(names), key=str.lower)
    if not names:
        return empty_text
    if len(names) <= MAX_LISTED_NAMES:
        return ", ".join(names)
    return "{}, ... (+{} more)".format(", ".join(names[:MAX_LISTED_NAMES]), len(names) - MAX_LISTED_NAMES)


def _allowed_workspace_ids(user):
    """Workspaces where `user` may use Custom Reports: those where the user is
    still a member and their "custom_report" permission is switched on (same
    per-module flag model as the rest of the app; superusers bypass it)."""
    member_ws = {
        str(ws_id) for ws_id in WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
    }
    granted_ws = {
        str(ws_id)
        for ws_id in UserPermission.objects.filter(user=user, custom_report=True).values_list(
            "workspace_id", flat=True
        )
    }
    return granted_ws & member_ws


def _resolve_workspace_scope(request, requested):
    """Workspace ids the caller may report on (None = unrestricted, for a
    superuser who didn't narrow to one). Returns a Response on 403."""
    user = request.user
    requested = requested or None

    if user.is_superuser:
        return [str(requested)] if requested else None

    allowed = _allowed_workspace_ids(user)
    if not allowed:
        return Response(
            {"error": "You don't have access to Custom Reports. Ask an admin to enable it for you."},
            status=403,
        )
    if requested:
        if str(requested) not in allowed:
            return Response({"error": "You do not have permission to report on this workspace."}, status=403)
        return [str(requested)]
    return sorted(allowed)


class CustomReportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            return self._build(request)
        except _BadRequest as exc:
            return Response({"error": str(exc)}, status=400)

    def _workspace_scope(self, request):
        return _resolve_workspace_scope(request, request.data.get("workspace"))

    def _build(self, request):
        data = request.data

        scope = self._workspace_scope(request)
        if isinstance(scope, Response):
            return scope
        workspace_ids = scope

        # ── Validate options ──────────────────────────────────────────────────
        date_from = _parse_date(data.get("date_from"), "date_from")
        date_to = _parse_date(data.get("date_to"), "date_to")
        if date_to < date_from:
            raise _BadRequest("date_to must be on or after date_from")
        range_days = (date_to - date_from).days + 1
        if range_days > MAX_RANGE_DAYS:
            raise _BadRequest(f"Date range can be at most {MAX_RANGE_DAYS} days")

        interval = data.get("interval")
        if interval not in INTERVALS:
            raise _BadRequest("interval must be one of: " + ", ".join(INTERVALS))
        if interval == "daily" and range_days > MAX_DAILY_RANGE_DAYS:
            raise _BadRequest(
                f"A daily report can cover at most {MAX_DAILY_RANGE_DAYS} days — choose weekly or biweekly "
                "for a longer range."
            )

        requested_metrics = set(_as_list(data.get("metrics"), "metrics"))
        if not requested_metrics or not requested_metrics <= set(METRICS_ORDER):
            raise _BadRequest("metrics must be a non-empty list of: " + ", ".join(METRICS_ORDER))
        metrics = [m for m in METRICS_ORDER if m in requested_metrics]

        group_by = _as_list(data.get("group_by"), "group_by")
        if not 1 <= len(group_by) <= 2 or len(set(group_by)) != len(group_by) or not set(group_by) <= set(GROUP_DIMS):
            raise _BadRequest("group_by must be 1 or 2 different values of: " + ", ".join(GROUP_DIMS))

        include_chart = bool(data.get("include_chart", False))
        chart_type = data.get("chart_type") or "bar"
        if include_chart and chart_type not in CHART_TYPES:
            raise _BadRequest("chart_type must be one of: " + ", ".join(CHART_TYPES))

        project_ids = _as_list(data.get("project_ids"), "project_ids")
        user_ids = _as_list(data.get("user_ids"), "user_ids")
        groups = _as_list(data.get("groups"), "groups")
        valid_groups = {value for value, _label in WorkspaceMember.GROUP_CHOICES}
        if not set(groups) <= valid_groups:
            raise _BadRequest("groups must be a list of: " + ", ".join(sorted(valid_groups)))

        # ── Resolve projects / employees the caller may actually see ─────────
        projects = []
        if project_ids:
            project_qs = Project.objects.filter(id__in=project_ids)
            if workspace_ids is not None:
                project_qs = project_qs.filter(workspace_id__in=workspace_ids)
            try:
                projects = list(project_qs)
            except DjangoValidationError:
                raise _BadRequest("project_ids contains an invalid id")
            if not projects:
                raise _BadRequest("None of the selected projects are available to you")
        project_set = {p.id for p in projects}

        # ── Entries: every project's hours for the relevant employees, over
        # the FULL Sun-Sat weeks covering the range, so Regular/OT is right. ─
        week_start, week_end = week_range_for(date_from, date_to)
        range_start, _ = envision_day_bounds_utc(week_start)
        _, range_end = envision_day_bounds_utc(week_end)

        base = TimeEntry.objects.filter(
            is_deleted=False, start_time__gte=range_start, start_time__lte=range_end,
        )
        if workspace_ids is not None:
            base = base.filter(workspace_id__in=workspace_ids)

        matching = base
        if project_set:
            matching = matching.filter(project_id__in=project_set)
        if user_ids:
            matching = matching.filter(user_id__in=user_ids)
        if groups:
            group_members = WorkspaceMember.objects.filter(group__in=groups)
            if workspace_ids is not None:
                group_members = group_members.filter(workspace_id__in=workspace_ids)
            matching = matching.filter(user_id__in=group_members.values("user_id"))
        try:
            target_user_ids = list(matching.values_list("user_id", flat=True).distinct())
        except DjangoValidationError:
            raise _BadRequest("project_ids/user_ids contains an invalid id")
        if not target_user_ids:
            return Response({"error": "No time entries found for the selected filters."}, status=404)

        entries = list(
            base.filter(user_id__in=target_user_ids).select_related("user", "project", "task")
        )

        group_lookup = None
        if "group" in group_by:
            members = WorkspaceMember.objects.filter(user_id__in=target_user_ids)
            if workspace_ids is not None:
                members = members.filter(workspace_id__in=workspace_ids)
            group_lookup = {(uid, wid): grp for uid, wid, grp in members.values_list("user_id", "workspace_id", "group")}

        buckets, date_to_bucket = build_buckets(date_from, date_to, interval)
        cells = aggregate_hours(
            entries, date_from, date_to, date_to_bucket, group_by,
            include_entry=(lambda e: e.project_id in project_set) if project_set else (lambda e: True),
            group_lookup=group_lookup,
        )
        if not cells:
            return Response({"error": "No time entries found for the selected filters."}, status=404)

        # ── Header block describing what was asked for ────────────────────────
        employee_names = (
            [u.get_full_name() or u.email for u in User.objects.filter(id__in=user_ids)] if user_ids else []
        )
        today = utc_to_envision_local(timezone.now()).date()
        summary_lines = [
            ("Date range", "{} - {}".format(date_from.strftime("%b %d, %Y"), date_to.strftime("%b %d, %Y"))),
            ("Interval", interval.capitalize()),
            ("Group by", " > ".join(GROUP_LABELS[g] for g in group_by)),
            ("Projects", _names_summary([p.name for p in projects], "All projects")),
            ("Employees", _names_summary(employee_names, "All employees")),
            ("User groups", _names_summary(
                [dict(WorkspaceMember.GROUP_CHOICES)[g] for g in groups], "All groups")),
            ("Metrics", ", ".join(METRIC_LABELS[m] for m in metrics)),
            ("Generated", "{} by {}".format(today.strftime("%b %d, %Y"),
                                            request.user.get_full_name() or request.user.email)),
        ]

        buffer = generate_custom_report_xlsx(
            cells=cells, buckets=buckets, metrics=metrics, group_by=group_by,
            summary_lines=summary_lines, include_chart=include_chart, chart_type=chart_type,
        )
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=f"Custom_Report_{date_from.isoformat()}_to_{date_to.isoformat()}.xlsx",
            content_type=XLSX_CONTENT_TYPE,
        )


class CustomReportOptionsView(APIView):
    """GET /api/reports/custom/options/ — projects and employees for the pickers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = _resolve_workspace_scope(request, request.query_params.get("workspace"))
        if isinstance(scope, Response):
            return scope

        projects = Project.objects.all()
        members = WorkspaceMember.objects.all()
        if scope is not None:
            projects = projects.filter(workspace_id__in=scope)
            members = members.filter(workspace_id__in=scope)

        users = User.objects.filter(id__in=members.values("user_id")).order_by("first_name", "last_name", "email")
        group_of = {}
        for uid, grp in members.exclude(group__isnull=True).values_list("user_id", "group"):
            group_of[uid] = grp
        group_counts = {}
        for grp in group_of.values():
            group_counts[grp] = group_counts.get(grp, 0) + 1
        return Response({
            "groups": [
                {"value": value, "label": label, "count": group_counts.get(value, 0)}
                for value, label in WorkspaceMember.GROUP_CHOICES
            ],
            "projects": [
                {"id": str(p.id), "name": p.name, "job_code": p.job_code or ""}
                for p in projects.order_by("name")
            ],
            "employees": [
                {"id": str(u.id), "label": u.get_full_name() or u.email, "group": group_of.get(u.id)}
                for u in users
            ],
        })
