"""
Envision GEO — Payroll Report API View
POST /api/reports/envision/payroll/
"""

from collections import OrderedDict
from datetime import datetime

from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils.envision_time import envision_day_bounds_utc
from projects.models import Project
from tasks.models import Task
from time_entries.models import TimeEntry
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceUser

from .envision_timesheet_utils import generate_envision_timesheet_xlsx


class EnvisionTimesheetReportView(APIView):
    """
    Generate and download an Envision GEO Payroll Excel report.

    Required body params:
        date_from    (str)    — start date, YYYY-MM-DD (inclusive)
        date_to      (str)    — end date,   YYYY-MM-DD (inclusive)

    Optional body params:
        project_id   (uuid)   — filter to a specific project (all projects if omitted)
        task_id      (uuid)   — filter to a specific task (requires project_id)
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        data = request.data

        # ── Validate required fields ──────────────────────────────────────────
        project_id = data.get("project_id")   # optional
        task_id    = data.get("task_id")       # optional
        from_str   = data.get("date_from")
        to_str     = data.get("date_to")

        if not all([from_str, to_str]):
            return Response(
                {"error": "date_from and date_to are required"},
                status=400,
            )

        try:
            date_from = datetime.strptime(from_str, "%Y-%m-%d").date()
            date_to   = datetime.strptime(to_str,   "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from"}, status=400)

        # ── Scope everything to the logged-in user's workspace(s) ─────────────
        # (same pattern as TimeEntryViewSet.get_queryset — see time_entries/views.py)
        user = request.user
        if user.is_superuser:
            workspace_ids = None  # superusers are not workspace-restricted
        else:
            workspace_ids = list(
                WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
            )
            if not workspace_ids:
                return Response({"error": "You are not a member of any workspace."}, status=403)

        # ── Fetch project (optional) ──────────────────────────────────────────
        project = None
        if project_id:
            try:
                project = Project.objects.select_related("client", "workspace").get(id=project_id)
            except Project.DoesNotExist:
                return Response({"error": "Project not found"}, status=404)

            if workspace_ids is not None and project.workspace_id not in workspace_ids:
                return Response({"error": "Project not found"}, status=404)

        # ── Fetch task (optional, only valid with a project) ──────────────────
        task = None
        if task_id:
            if not project:
                return Response(
                    {"error": "project_id is required when task_id is provided"},
                    status=400,
                )
            try:
                task = Task.objects.get(id=task_id, project=project)
            except Task.DoesNotExist:
                return Response({"error": "Task not found for this project"}, status=404)

        # ── Fetch time entries ─────────────────────────────────────────────────
        # date_from/date_to are Mountain-Time (Envision GEO) calendar days —
        # match them against UTC-stored start_time using explicit MT bounds.
        range_start, _ = envision_day_bounds_utc(date_from)
        _, range_end = envision_day_bounds_utc(date_to)
        entries_qs = TimeEntry.objects.filter(
            start_time__gte=range_start,
            start_time__lte=range_end,
        )
        if workspace_ids is not None:
            entries_qs = entries_qs.filter(workspace_id__in=workspace_ids)
        if project_id:
            entries_qs = entries_qs.filter(project_id=project_id)
        if task_id:
            entries_qs = entries_qs.filter(task_id=task_id)

        entries = (
            entries_qs
            .select_related("user")
            .order_by("user__first_name", "user__last_name", "start_time")
        )

        if not entries.exists():
            return Response(
                {
                    "error": "No time entries found for the given date range.",
                    "date_from": from_str,
                    "date_to":   to_str,
                },
                status=404,
            )

        # ── Group entries by user (preserving order) ──────────────────────────
        entries_by_user = OrderedDict()
        for entry in entries:
            name = entry.user.get_full_name() or entry.user.email
            entries_by_user.setdefault(name, []).append(entry)

        # ── Resolve display labels ────────────────────────────────────────────
        project_name = project.name if project else "All Projects"
        task_name    = task.name if task else ("All Tasks" if not task_id else "")

        # ── Generate Excel ────────────────────────────────────────────────────
        xlsx_buffer = generate_envision_timesheet_xlsx(
            entries_by_user=entries_by_user,
            project_name=project_name,
            task_name=task_name,
            date_from=date_from,
            date_to=date_to,
        )

        filename = "Envision_Payroll_{}_to_{}.xlsx".format(from_str, to_str)

        return FileResponse(
            xlsx_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
