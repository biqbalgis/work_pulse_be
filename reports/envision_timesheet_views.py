"""
Envision GEO — Timesheet Report API View
POST /api/reports/envision/timesheet/
"""

from collections import OrderedDict
from datetime import datetime

from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.models import Project
from tasks.models import Task
from time_entries.models import TimeEntry
from workspaces.permissions import IsWorkspaceUser

from .envision_timesheet_utils import generate_envision_timesheet_xlsx


class EnvisionTimesheetReportView(APIView):
    """
    Generate and download an Envision GEO Timesheet Excel report.

    Required body params:
        project_id   (uuid)   — project to report on
        task_id      (uuid)   — task within the project
        date_from    (str)    — start date, YYYY-MM-DD (inclusive)
        date_to      (str)    — end date,   YYYY-MM-DD (inclusive)
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        data = request.data

        # ── Validate required fields ──────────────────────────────────────────
        project_id = data.get("project_id")
        task_id    = data.get("task_id")
        from_str   = data.get("date_from")
        to_str     = data.get("date_to")

        if not all([project_id, task_id, from_str, to_str]):
            return Response(
                {"error": "project_id, task_id, date_from and date_to are required"},
                status=400,
            )

        try:
            date_from = datetime.strptime(from_str, "%Y-%m-%d").date()
            date_to   = datetime.strptime(to_str,   "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from"}, status=400)

        # ── Fetch project & task ──────────────────────────────────────────────
        try:
            project = Project.objects.select_related("client", "workspace").get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=404)

        try:
            task = Task.objects.get(id=task_id, project=project)
        except Task.DoesNotExist:
            return Response({"error": "Task not found for this project"}, status=404)

        # ── Fetch time entries ────────────────────────────────────────────────
        entries = (
            TimeEntry.objects
            .filter(
                project_id=project_id,
                task_id=task_id,
                start_time__date__gte=date_from,
                start_time__date__lte=date_to,
            )
            .select_related("user")
            .order_by("user__first_name", "user__last_name", "start_time")
        )

        if not entries.exists():
            return Response(
                {
                    "error": "No time entries found for the given project, task and date range.",
                    "project_id": str(project_id),
                    "task_id":    str(task_id),
                    "date_from":  from_str,
                    "date_to":    to_str,
                },
                status=404,
            )

        # ── Group entries by user (preserving order) ──────────────────────────
        entries_by_user = OrderedDict()
        for entry in entries:
            name = entry.user.get_full_name() or entry.user.email
            entries_by_user.setdefault(name, []).append(entry)

        # ── Generate Excel ────────────────────────────────────────────────────
        xlsx_buffer = generate_envision_timesheet_xlsx(
            entries_by_user=entries_by_user,
            project_name=project.name,
            task_name=task.name,
            date_from=date_from,
            date_to=date_to,
        )

        filename = "Envision_Timesheet_{}_to_{}.xlsx".format(from_str, to_str)

        return FileResponse(
            xlsx_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
