from datetime import datetime

from django.shortcuts import render
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.models import Sum
from django.db.models.functions import TruncDate

from projects.models import Project
from time_entries.models import TimeEntry
from workspaces.permissions import IsWorkspaceManager, IsSuperUser


# Create your views here.

class WeeklyPayrollReport(APIView):
    permission_classes = [IsAuthenticated,IsWorkspaceManager | IsSuperUser]

    def post(self, request):
        from_date = request.data.get("from")
        to_date = request.data.get("to")

        if not from_date or not to_date:
            return Response({"error": "from and to dates are required"}, status=400)

        from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date = datetime.strptime(to_date, "%Y-%m-%d").date()

        # Active projects only
        active_project_ids = Project.objects.filter(is_active=True).values_list("id", flat=True)

        # Aggregate hours by user across the whole week
        weekly_entries = (
            TimeEntry.objects.filter(
                start_time__date__range=[from_date, to_date],
                project_id__in=active_project_ids
            )
            .values("user_id", "user__first_name", "user__last_name")
            .annotate(total_minutes=Sum("duration"))
        )

        results = []

        for entry in weekly_entries:
            total_hours = entry["total_minutes"] / 60.0

            # Weekly RT limit = 40 hours (Sun to Sat)
            rt = min(total_hours, 40)
            ot = max(total_hours - 40, 0)

            results.append({
                "employee_id": entry["user_id"],
                "employee_name": f"{entry['user__first_name']} {entry['user__last_name']}",
                "total_hours": round(total_hours, 2),
                "total_rt": round(rt, 2),
                "total_ot": round(ot, 2),
            })

        return Response(results, status=status.HTTP_200_OK)