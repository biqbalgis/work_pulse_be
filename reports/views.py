from datetime import datetime, timedelta

from django.http import FileResponse
from django.utils import timezone
from django.shortcuts import render
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.models import Sum
from django.db.models.functions import TruncDate

from approvals.models import TimeEntryApprovalItem
from approvals.utils import calculate_rt_ot_and_cost
from projects.models import Project
from time_entries.models import TimeEntry
from users.models import User
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser, IsWorkspaceUser
from organization_asset.models import AssetUsage
from .models import LEMReport
from .excel_utils import generate_time_entry_report
import io
from django.http import HttpResponse, FileResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

# Create your views here.
def generate_lem_pdf(report):
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    elements = []

    # -------------------------
    # HEADER
    # -------------------------
    title = Paragraph(
        f"<b>LEM Report — {report['lem_number']}</b>",
        styles["Heading1"]
    )
    elements.append(title)
    elements.append(Spacer(1, 20))

    header_data = [
        ["Project:", report["project_name"]],
        ["Reporting Period:", f"{report['from_date']} → {report['to_date']}"],
        ["Requester:", report.get("requester", "")]
    ]

    header_table = Table(header_data, colWidths=[150, 500])

    header_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("PADDING", (0, 0), (-1, -1), 6)
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 30))

    # -------------------------
    # DAILY TABLES
    # -------------------------
    for day, day_data in report["report_data"].items():

        # day title
        elements.append(Paragraph(f"<b>Date: {day}</b>", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        table_data = [
            ["Name", "Role", "Hours", "Supplies", "Equipment"]
        ]

        for entry in day_data["time_entries"]:
            table_data.append([
                entry["name"],
                entry["role"],
                entry["hours"],
                entry["supplies"],
                entry["equip"]
            ])

        # add blank rows like your sheet
        for _ in range(8):
            table_data.append(["", "", "", "", ""])

        crew_table = Table(
            table_data,
            colWidths=[220, 150, 100, 100, 150]
        )

        crew_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (2, 1), (-1, -1), "CENTER"),
            ("ROWHEIGHT", (0, 0), (-1, -1), 25),
        ]))

        elements.append(crew_table)
        elements.append(Spacer(1, 40))

    doc.build(elements)

    buffer.seek(0)
    return buffer

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


class DailyWorkReportView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def get(self, request):
        date_str = request.GET.get("date")
        workspace_id = request.GET.get("workspace_id")
        project_id = request.GET.get("project_id")

        if not date_str:
            return Response({"error": "Date is required"}, status=400)

        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        filters = {"start_time__date": date}
        if workspace_id:
            filters["workspace_id"] = workspace_id
        if project_id:
            filters["project_id"] = project_id

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



class DailyWorkReportView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        return self.get(request)

    def get(self, request):
        # Support both GET query params and POST body
        data = request.GET if request.method == 'GET' else request.data
        
        date_str = data.get("date")
        from_date_str = data.get("from")
        to_date_str = data.get("to")
        
        workspace_id = data.get("workspace_id")
        project_id = data.get("project_id")

        # Determine date range
        start_date = None
        end_date = None

        try:
            if from_date_str and to_date_str:
                start_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
            elif date_str:
                start_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                end_date = start_date
            else:
                return Response({"error": "Either 'date' or 'from' and 'to' parameters are required"}, status=400)
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        # Base filter
        filters = {"start_time__date__range": [start_date, end_date]}
        if workspace_id:
            filters["workspace_id"] = workspace_id
        if project_id:
            filters["project_id"] = project_id

        # Fetch Time Entries with related data
        time_entries = TimeEntry.objects.filter(**filters).select_related(
            "user", "job_title", "project"
        ).prefetch_related("asset_usages", "asset_usages__asset").order_by("start_time")

        # Process data day by day
        report_data_by_date = {}
        
        # Iterate through all days in range to ensure empty days are present if needed, 
        # or just iterate through entries if we only want days with data.
        # User asked for "date wise json response".
        
        # Group entries by date
        entries_by_date = {}
        for entry in time_entries:
            d_str = str(entry.start_time.date())
            if d_str not in entries_by_date:
                entries_by_date[d_str] = []
            entries_by_date[d_str].append(entry)

        for d_str, day_entries in entries_by_date.items():
            # Data Containers for this day
            entries_map = {} # Key: (user_id, job_title_id)
            descriptions = []
            
            # Cost Aggregation
            personnel_costs = {}  # Key: Job Title Name
            asset_costs = {}      # Key: Asset Name

            for entry in day_entries:
                # 1. Entries Aggregation
                user_id = entry.user.id
                role_id = entry.job_title.id if entry.job_title else None
                key = (user_id, role_id)
                
                user_name = entry.user.get_full_name()
                role_name = entry.job_title.name if entry.job_title else "N/A"
                hours = round(entry.duration / 60.0, 2)

                if key not in entries_map:
                    entries_map[key] = {
                        "name": user_name,
                        "role": role_name,
                        "supplies": 0,
                        "equip": 0,
                        "hours": 0
                    }

                entries_map[key]["hours"] += hours
                
                # Count assets for this entry
                for usage in entry.asset_usages.all():
                    asset = usage.asset
                    if asset.charge_type == "quantity":
                        entries_map[key]["supplies"] += 1
                        
                        # Asset Cost Aggregation
                        if asset.name not in asset_costs:
                            asset_costs[asset.name] = {
                                "item": asset.name,
                                "hours": 0.0,
                                "units": 0.0,
                                "rate": round(float(asset.quantity_rate), 2) if asset.quantity_rate else 0.0,
                                "cost": 0.0
                            }
                        asset_costs[asset.name]["units"] += round(float(usage.quantity_used or 0), 2)
                        asset_costs[asset.name]["cost"] += round(float(usage.cost), 2)

                    elif asset.charge_type == "hourly":
                        entries_map[key]["equip"] += 1
                        
                        # Asset Cost Aggregation
                        if asset.name not in asset_costs:
                            asset_costs[asset.name] = {
                                "item": asset.name,
                                "hours": 0.0,
                                "units": 0.0,
                                "rate": round(float(asset.hourly_rate), 2) if asset.hourly_rate else 0.0,
                                "cost": 0.0
                            }
                        asset_costs[asset.name]["hours"] += hours
                        asset_costs[asset.name]["cost"] += round(float(usage.cost), 2)

                # 2. Descriptions
                if entry.description:
                    descriptions.append(entry.description)

                # 3. Personnel Cost Aggregation
                if role_name not in personnel_costs:
                    personnel_costs[role_name] = {
                        "item": role_name,
                        "hours": 0.0,
                        "units": 0, 
                        "rate": round(float(entry.hourly_rate), 2) if entry.hourly_rate else 0.0,
                        "cost": 0.0
                    }
                personnel_costs[role_name]["hours"] += hours
                personnel_costs[role_name]["cost"] += round(float(entry.cost), 2)

            # Convert entries map to list
            entries_list = []
            for data in entries_map.values():
                data["hours"] = round(data["hours"], 2)
                entries_list.append(data)

            # Format Cost Summary
            cost_summary = []
            
            # Add Personnel
            for role, data in personnel_costs.items():
                cost_summary.append({
                    "item": data["item"],
                    "hours": round(data["hours"], 2),
                    "units": None,
                    "rate": data["rate"],
                    "cost": round(data["cost"], 2)
                })
                
            # Add Assets
            for asset, data in asset_costs.items():
                cost_summary.append({
                    "item": data["item"],
                    "hours": round(data["hours"], 2) if data["hours"] > 0 else None,
                    "units": round(data["units"], 2) if data["units"] > 0 else None,
                    "rate": data["rate"],
                    "cost": round(data["cost"], 2)
                })

            report_data_by_date[d_str] = {
                "date": d_str,
                "time_entries": entries_list,
                "descriptions": descriptions,
                "cost_summary": cost_summary
            }

        # Create LEM Report (or reuse existing for same project + date)
        from .models import LEMReport

        project_obj = None
        if project_id:
            project_obj = Project.objects.filter(id=project_id).first()

        existing_lem = LEMReport.objects.filter(
            project=project_obj,
            lem_date=start_date,
            lem_number__startswith="LEM-",
        ).first() if project_obj else None

        if existing_lem:
            lem_report = existing_lem
        else:
            lem_report = LEMReport.objects.create(
                requester=request.user,
                project=project_obj,
                lem_date=start_date,
                report_data={},
            )

        response_data = {
            "lem_number": lem_report.lem_number,
            "requester": f"{request.user.first_name} {request.user.last_name}",
            "project_name": project_obj.name if project_obj else "N/A",
            "report_data": report_data_by_date
        }
        
        # Save complete JSON to DB
        lem_report.report_data = response_data
        lem_report.save()

        return Response(response_data, status=status.HTTP_200_OK)


class LEMReportGenerationView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        from_date_str = request.data.get("from")
        to_date_str = request.data.get("to")
        project_id = request.data.get("project_id")

        if not from_date_str or not to_date_str:
            return Response({"error": "from and to dates are required"}, status=400)
        
        if not project_id:
             return Response({"error": "project_id is required"}, status=400)

        try:
            start_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
             return Response({"error": "Project not found"}, status=404)

        existing_lem = LEMReport.objects.filter(
            project=project,
            lem_date=start_date,
            lem_number__startswith="LEM-",
        ).first()

        if existing_lem:
            lem_report = existing_lem
        else:
            lem_report = LEMReport.objects.create(
                requester=request.user,
                project=project,
                lem_date=start_date,
            )

        daily_reports = []
        current_date = start_date
        
        all_time_entries = TimeEntry.objects.filter(
            project_id=project_id,
            start_time__date__range=[start_date, end_date]
        ).select_related('user', 'job_title').prefetch_related('asset_usages__asset')

        entries_by_date = {}
        for entry in all_time_entries:
            d = entry.start_time.date()
            if d not in entries_by_date:
                entries_by_date[d] = []
            entries_by_date[d].append(entry)

        while current_date <= end_date:
            day_entries = entries_by_date.get(current_date, [])
            
            user_entries = {}
            for entry in day_entries:
                if entry.user_id not in user_entries:
                    user_entries[entry.user_id] = {
                        "user": entry.user,
                        "total_minutes": 0
                    }
                user_entries[entry.user_id]["total_minutes"] += entry.duration

            employees_data = []
            for user_id, data in user_entries.items():
                total_hours = data["total_minutes"] / 60.0
                rt_hours = min(total_hours, 8.0)
                ot_hours = max(total_hours - 8.0, 0.0)
                
                employees_data.append({
                    "id": user_id,
                    "name": f"{data['user'].first_name} {data['user'].last_name}",
                    "total_hours": round(total_hours, 2),
                    "regular_hours": round(rt_hours, 2),
                    "overtime_hours": round(ot_hours, 2)
                })

            assets_map = {}
            for entry in day_entries:
                for usage in entry.asset_usages.all():
                    asset = usage.asset
                    if asset.id not in assets_map:
                        assets_map[asset.id] = {
                            "id": str(asset.id),
                            "name": asset.name,
                            "usage": 0.0,
                            "unit": "hours" if asset.charge_type == 'hourly' else "quantity"
                        }
                    
                    if asset.charge_type == 'hourly':
                        assets_map[asset.id]["usage"] += round(entry.duration / 60.0, 2)
                    else:
                        assets_map[asset.id]["usage"] += round(float(usage.quantity_used or 0), 2)

            assets_data = []
            for asset_vals in assets_map.values():
                assets_data.append({
                    "id": asset_vals["id"],
                    "name": asset_vals["name"],
                    "usage": round(asset_vals["usage"], 2),
                    "unit": asset_vals["unit"]
                })

            daily_reports.append({
                "date": str(current_date),
                "employees": employees_data,
                "assets": assets_data
            })
            
            current_date += timedelta(days=1)

        result = {
            "lem_number": lem_report.lem_number,
            "project_id": project_id,
            "project_name": project.name,
            "from_date": from_date_str,
            "to_date": to_date_str,
            "daily_reports": daily_reports
        }
        
        lem_report.report_data = result
        lem_report.save()

        # pdf_buffer = generate_lem_pdf(result)

        return Response(result, status=status.HTTP_201_CREATED)
        # return FileResponse(
        #     pdf_buffer,
        #     as_attachment=True,
        #     filename=f"{lem_report.lem_number}.pdf",
        #     content_type="application/pdf"
        # )


from .pdf_utils import generate_daily_lem_pdf
from django.http import FileResponse

class LEMDailyReportView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        date_str = request.data.get("date")
        project_id = request.data.get("project_id")
        sign = request.data.get("sign",None)
        sign_name = request.data.get("sign_name", "")
        sign_date = request.data.get("sign_date", "")
        if not date_str or not project_id:
            return Response(
                {"error": "date and project_id required"},
                status=400
            )

        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date"}, status=400)

        project = get_object_or_404(Project, id=project_id)

        entries = (
            TimeEntry.objects
            .filter(
                project_id=project_id,
                start_time__date=report_date
            )
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
        )

        rows = []

        for entry in entries:
            start = entry.start_time.strftime("%H:%M")
            end = entry.end_time.strftime("%H:%M") if entry.end_time else ""

            extras = []

            for usage in entry.asset_usages.all():
                extras.append(usage.asset.name)

            rows.append({
                "name": f"{entry.user.first_name} {entry.user.last_name}",
                "role": entry.job_title.name if entry.job_title else "",
                "start": start,
                "end": end,
                "hours": round(entry.duration / 60, 2),
                "extras": ", ".join(extras)
            })

        # Reuse existing LEM for same project + date, or create new
        existing_lem = LEMReport.objects.filter(
            project=project,
            lem_date=report_date,
            lem_number__startswith="LEM-",
        ).first()

        if existing_lem:
            lem_report = existing_lem
        else:
            lem_report = LEMReport.objects.create(
                requester=request.user,
                project=project,
                lem_date=report_date,
            )

        if sign and not sign_name:
            sign_name = f"{request.user.first_name} {request.user.last_name}".strip()
        if sign and not sign_date:
            sign_date = timezone.localdate().isoformat()

        result = {
            "project_name": project.name,
            "client_name": project.client.name if project.client else "",
            "date": date_str,
            "lem_number": lem_report.lem_number,
            "sign": sign,
            "sign_name": sign_name,
            "sign_date": sign_date,
            "rows": rows
        }

        lem_report.report_data = result
        lem_report.save()

        if request.data.get("generate_pdf"):
            pdf_buffer = generate_daily_lem_pdf(result)
            return FileResponse(
                pdf_buffer,
                as_attachment=True,
                filename=f"Daily_LEM_{date_str}.pdf",
                content_type="application/pdf"
            )

        return Response(result)


from .cost_calculator import CostCalculator
from .pdf_utils import generate_costing_lem_pdf

class LEMCostingReportView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceManager]

    def post(self, request):
        date_str = request.data.get("date")
        project_id = request.data.get("project_id")
        sign = request.data.get("sign", None)
        sign_name = request.data.get("sign_name", "")
        sign_date = request.data.get("sign_date", "")
        if not date_str or not project_id:
            return Response(
                {"error": "date and project_id required"},
                status=400
            )

        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date"}, status=400)

        project = get_object_or_404(Project, id=project_id)

        # 1. Identify Users working on this Project on this Date
        daily_entries = TimeEntry.objects.filter(
            project_id=project_id,
            start_time__date=report_date
        ).select_related("user", "job_title")

        user_ids_on_day = daily_entries.values_list("user_id", flat=True).distinct()
        
        report_rows = []
        
        # Calculate Week Start (Monday)
        weekday_idx = report_date.weekday() # 0=Mon, 6=Sun
        days_since_monday = weekday_idx
        monday_date = report_date - timedelta(days=days_since_monday)
        
        # Fetch M-F entries for relevant users (Global)
        week_entries_qs = TimeEntry.objects.filter(
            user_id__in=user_ids_on_day,
            start_time__date__range=[monday_date, monday_date + timedelta(days=4)], # Mon-Fri
        )
        
        user_weekly_hours = {}
        for e in week_entries_qs:
            dur = e.duration / 60.0
            user_weekly_hours[e.user_id] = user_weekly_hours.get(e.user_id, 0.0) + dur
            
        # 2. Iterate Users
        users = User.objects.filter(id__in=user_ids_on_day)
        
        for user in users:
            calculator = CostCalculator(user, report_date, project)
            
            # Fetch generic daily entries for THIS project to calculate its specific costs
            this_project_entries = [e for e in daily_entries if e.user_id == user.id]
            
            weekly_sum = user_weekly_hours.get(user.id, 0.0)
            
            cost_map = calculator.calculate_daily_cost_for_user(this_project_entries, weekly_sum)
            
            for jt_name, data in cost_map.items():
                report_rows.append({
                    "employee_name":    f"{user.first_name} {user.last_name}",
                    "job_title":        jt_name,
                    "regular_hours":    round(float(data["reg"]),             2),
                    "regular_rate":     round(float(data.get("reg_rate", 0)), 2),
                    "overtime_hours":   round(float(data["ot"]),              2),
                    "overtime_rate":    round(float(data.get("ot_rate", 0)),  2),
                    "double_time_hours":round(float(data["dt"]),              2),
                    "double_time_rate": round(float(data.get("dt_rate", 0)),  2),
                    "per_hour_cost":    round(float(data["base_rate"]),       2),
                    "total_cost":       round(float(data["cost"]),            2),
                })

        # Reuse existing LEM for same project + date, or create new
        existing_lem = LEMReport.objects.filter(
            project=project,
            lem_date=report_date,
            lem_number__startswith="LEM-",
        ).first()

        if existing_lem:
            lem_report = existing_lem
        else:
            lem_report = LEMReport.objects.create(
                requester=request.user,
                project=project,
                lem_date=report_date,
            )

        if sign and not sign_name:
            sign_name = f"{request.user.first_name} {request.user.last_name}".strip()
        if sign and not sign_date:
            sign_date = timezone.localdate().isoformat()

        # Prepare Result
        result = {
            "project_name": project.name,
            "client_name": project.client.name if project.client else "",
            "date": date_str,
            "lem_number": lem_report.lem_number,
            "sign": sign,
            "sign_name": sign_name,
            "sign_date": sign_date,
            "rows": report_rows
        }
        
        # Save report data
        lem_report.report_data = result
        lem_report.save()

        # Generate PDF
        pdf_buffer = generate_costing_lem_pdf(result)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Daily_LEM_Costing_{date_str}.pdf",
            content_type="application/pdf"
        )

class TimeEntryExcelReportView(APIView):
    permission_classes = [IsAuthenticated, IsWorkspaceManager | IsSuperUser]

    def post(self, request):
        from_date_str = request.data.get("from")
        to_date_str = request.data.get("to")
        project_id = request.data.get("project_id")

        if not from_date_str or not to_date_str:
            return Response({"error": "from and to dates are required"}, status=400)

        try:
            start_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        try:
            excel_file = generate_time_entry_report(start_date, end_date, project_id)

            filename = f"Time_Entry_Report_{start_date}_to_{end_date}.xlsx"
            if project_id:
                filename = f"Project_{project_id}_Time_Report_{start_date}_to_{end_date}.xlsx"

            response = HttpResponse(
                excel_file.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=500)
