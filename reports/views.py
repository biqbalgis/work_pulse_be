from datetime import datetime, timedelta

from django.shortcuts import render
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.models import Sum
from django.db.models.functions import TruncDate

from approvals.models import TimeEntryApprovalItem
from approvals.utils import calculate_rt_ot_and_cost
from projects.models import Project
from time_entries.models import TimeEntry
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser, IsWorkspaceUser


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


class DailyRTOTReport(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        from_date = request.data.get("from")
        to_date = request.data.get("to")

        if not from_date or not to_date:
            return Response({"error": "from and to dates are required"}, status=400)

        from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date = datetime.strptime(to_date, "%Y-%m-%d").date()

        user = request.user

        # 1) Filter only ACTIVE projects
        active_ids = Project.objects.filter(is_active=True).values_list("id", flat=True)

        # 2) Filter only APPROVED time entries (for payroll calculation)
        # approved_ids = TimeEntryApprovalItem.objects.filter(
        #     approved=True
        # ).values_list("time_entry_id", flat=True)

        # 3) Base query
        base_query = TimeEntry.objects.filter(
            # id__in=approved_ids,
            start_time__date__range=[from_date, to_date],
            project_id__in=active_ids
        )

        # 4) Role check (admin/superuser can see all, normal user only see own)
        is_admin_or_super = user.is_superuser or WorkspaceMember.objects.filter(
            user=user, role="admin"
        ).exists()

        if not is_admin_or_super:
            base_query = base_query.filter(user=user)

        # 5) Group and summarize
        entries = (
            base_query
            .annotate(date=TruncDate("start_time"))
            .values("date", "user_id", "user__first_name", "user__last_name")
            .annotate(total_minutes=Sum("duration"))
            .order_by("date", "user_id")
        )

        # 6) Get descriptions grouped by user + date
        desc_lookup = {}
        for t in base_query:
            key = (t.start_time.date(), t.user_id)
            desc_lookup.setdefault(key, [])
            if t.description:
                desc_lookup[key].append(t.description)

        result = {}

        for e in entries:
            date_str = str(e["date"])
            hrs = e["total_minutes"] / 60.0

            # RT/OT rules for daily payroll
            rt = min(hrs, 8.0)
            ot = max(hrs - 8.0, 0.0)

            key = (e["date"], e["user_id"])
            descriptions = desc_lookup.get(key, [])

            emp_data = {
                "employee_id": e["user_id"],
                "employee_name": f"{e['user__first_name']} {e['user__last_name']}",
                "total_hours": round(hrs, 2),
                "rt_hours": round(rt, 2),
                "ot_hours": round(ot, 2),
                "descriptions": descriptions
            }

            result.setdefault(date_str, []).append(emp_data)

        return Response(result, status=status.HTTP_200_OK)


class DailyDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def get(self, request, employee_id, date):
        from datetime import datetime
        from time_entries.models import TimeEntry
        from approvals.models import TimeEntryApprovalItem

        # Parse date
        date = datetime.strptime(date, "%Y-%m-%d").date()

        # Fetch ALL entries for that date
        entries = list(TimeEntry.objects.filter(
            user_id=employee_id,
            start_time__date=date
        ).select_related("project", "job_title").order_by("start_time"))

        # Calculate approval status map
        approval_map = {
            a.time_entry_id: a.approved
            for a in TimeEntryApprovalItem.objects.filter(
                time_entry_id__in=[e.id for e in entries]
            )
        }

        # ----------- RT/OT CALCULATION -------------
        total_minutes = sum(e.duration for e in entries)
        total_hours = total_minutes / 60.0

        remaining_rt = 8.0  # daily limit

        def allocate_rt_ot(entry_minutes):
            nonlocal remaining_rt
            hours = entry_minutes / 60.0

            if remaining_rt <= 0:
                return 0.0, hours  # all OT

            rt_hours = min(hours, remaining_rt)
            remaining_rt -= rt_hours
            ot_hours = hours - rt_hours
            return rt_hours, ot_hours

        # ---------------------------------------------

        result = {
            "date": str(date),
            "employee_id": employee_id,
            "entries": []
        }

        for e in entries:
            rt, ot = allocate_rt_ot(e.duration)

            result["entries"].append({
                "id": str(e.id),
                "start_time": e.start_time.strftime("%H:%M"),
                "end_time": e.end_time.strftime("%H:%M") if e.end_time else None,
                "duration_hours": round(e.duration / 60.0, 2),
                "rt_hours": round(rt, 2),
                "ot_hours": round(ot, 2),
                "project": e.project.name if e.project else None,
                "billable": e.billable,
                "meals": getattr(e, "meals", False),
                "hotels": getattr(e, "hotels", False),
                "job_title": e.job_title.name if e.job_title else None,
                "approved": approval_map.get(e.id, None),
                "description": e.description
            })

        return Response(result, status=200)



class EmployeePayrollDashboard(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.GET.get("period", "week")
        start = request.GET.get("start")
        end = request.GET.get("end")

        # If no custom dates, auto-detect current week/month
        today = datetime.utcnow().date()

        if not start or not end:
            if period == "month":
                start = today.replace(day=1)
                end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            else:  # weekly (Sunday -> Saturday)
                days_until_sunday = (today.weekday() + 1) % 7
                start = today - timedelta(days=days_until_sunday)
                end = start + timedelta(days=6)
        else:
            start = datetime.strptime(start, "%Y-%m-%d").date()
            end = datetime.strptime(end, "%Y-%m-%d").date()

        # Fetch only APPROVED items
        items = TimeEntryApprovalItem.objects.filter(
            approval__status="approved",
            time_entry__start_time__date__range=[start, end]
        ).select_related("time_entry__user", "time_entry__project")

        # Group by employee
        employees = {}
        for item in items:
            u = item.time_entry.user
            if u.id not in employees:
                employees[u.id] = {
                    "employee_id": u.id,
                    "employee_name": u.get_full_name(),
                    "items": []
                }
            employees[u.id]["items"].append(item)

        # Calculate metrics using existing helper
        result = []
        for emp in employees.values():
            metrics = calculate_rt_ot_and_cost(emp["items"])
            result.append({
                "employee_id": emp["employee_id"],
                "employee_name": emp["employee_name"],
                "hours": {
                    "total": metrics["total_hours"],
                    "regular": metrics["rt_hours"],
                    "overtime": metrics["ot_hours"]
                },
                "cost": {
                    "total": metrics["total_cost"],
                    "regular_cost": metrics.get("rt_cost", 0),
                    "overtime_cost": metrics.get("ot_cost", 0)
                }
            })

        return Response(result)