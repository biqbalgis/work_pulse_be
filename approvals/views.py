from datetime import datetime

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceUser
from .models import TimeEntryApproval, TimeEntryApprovalItem
from .serializers import TimeEntryApprovalSerializer, TimeEntryApprovalItemSerializer
from time_entries.models import TimeEntry
from .utils import can_approve, calculate_rt_ot_and_cost


class TimeEntryApprovalViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntryApprovalSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def get_queryset(self):
        user = self.request.user
        queryset = TimeEntryApproval.objects.filter(is_deleted=False).select_related("user", "workspace")

        if not user.is_superuser:
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
            queryset = queryset.filter(workspace_id__in=workspace_ids)

        workspace_id = self.request.query_params.get("workspace")
        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)

        approval_status = self.request.query_params.get("status")
        if approval_status:
            queryset = queryset.filter(status=approval_status)

        return queryset

    # ========== EMPLOYEE SUBMITS WEEK ==========
    def list(self, request, *args, **kwargs):
        approvals = self.get_queryset()

        data = []
        for approval in approvals:
            items = approval.items.select_related("time_entry__project")
            metrics = calculate_rt_ot_and_cost(items, workspace=approval.workspace)

            data.append({
                "id": approval.id,
                "employee": {
                    "id": approval.user.id,
                    "name": approval.user.get_full_name()
                },
                "period": {
                    "start_date": approval.start_date,
                    "end_date": approval.end_date,
                    "type": "day" if approval.start_date == approval.end_date else "week"
                },
                "entries_count": items.count(),
                "hours": {
                    "total": metrics["total_hours"],
                    "regular": metrics["rt_hours"],
                    "overtime": metrics["ot_hours"]
                },
                "cost": {
                    "total": metrics["total_cost"]
                },
                "status": approval.status,
                "can_approve": can_approve(request.user, approval.user, approval.workspace)
            })

        return Response(data)

    @action(detail=False, methods=["post"], url_path="submit")
    def submit_for_approval(self, request):
        user = request.user
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        if not start_date or not end_date:
            return Response({"error": "start_date and end_date are required"}, status=400)

        # Convert to date objects
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

        # 1) Fetch unlocked time entries only (pending)
        pending_entries = TimeEntry.objects.filter(
            user=user,
            start_time__date__range=[start_date_obj, end_date_obj],
            is_locked=False
        )

        if not pending_entries.exists():
            return Response(
                {"message": "No pending entries to submit in selected date range."},
                status=400
            )

        # 2) Create an approval for ONLY pending entries
        approval = TimeEntryApproval.objects.create(
            workspace=user.workspace,
            user=user,
            start_date=start_date_obj,
            end_date=end_date_obj,
            created_by=user,
            status="submitted"
        )

        # 3) Create approval items for each pending entry
        submitted_dates = set()
        total_minutes = 0

        for e in pending_entries:
            TimeEntryApprovalItem.objects.create(
                approval=approval,
                time_entry=e,
                approved=True,  # default as pending approval
                created_by=user
            )
            submitted_dates.add(e.start_time.date())
            total_minutes += e.duration

        # 4) Save total hours on parent approval record
        approval.total_hours = round(total_minutes / 60.0, 2)
        approval.save()

        return Response({
            "message": f"Submitted {len(submitted_dates)} day(s) for approval.",
            "approval_id": approval.id,
            "submitted_dates": sorted(list(submitted_dates)),
            "total_hours": approval.total_hours,
            "status": "submitted"
        }, status=200)

    # ========== APPROVE WEEK ==========
    @action(detail=True, methods=["post"], url_path="approve-week")
    def approve_week(self, request, pk=None):
        approval = self.get_object()

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "You are not authorized to approve this employee's time."}, status=403)

        # Set all items to approved + lock time entries
        for item in approval.items.all():
            item.approved = True
            item.save()
            e = item.time_entry
            e.is_locked = True
            e.save()

        approval.status = "approved"
        approval.reviewed_by = request.user
        approval.save()

        return Response({"message": "Week approved successfully."})

    # ========== REJECT WEEK ==========
    @action(detail=True, methods=["post"], url_path="approve-week")
    def approve_week(self, request, pk=None):
        approval = self.get_object()

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "You are not authorized to approve this employee's time."}, status=403)

        # Approve & lock all entries
        for item in approval.items.all():
            item.approved = True
            item.save()

            e = item.time_entry
            e.is_locked = True  # 🔐 Lock the time entry
            e.save()

        approval.status = "approved"
        approval.reviewed_by = request.user
        approval.save()

        return Response({"message": "Week approved successfully."})

    # ========== APPROVE / REJECT SINGLE ENTRY ==========
    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/approve")
    def approve_entry(self, request, pk=None, item_id=None):
        item = TimeEntryApprovalItem.objects.get(id=item_id)
        approval = item.approval

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)

        if approval.status == "approved":
            return Response({"error": "This week is already approved."}, status=400)

        item.approved = True
        item.save()

        e = item.time_entry
        e.is_locked = True
        e.save()

        return Response({"message": "Entry approved."})

    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/reject")
    def reject_entry(self, request, pk=None, item_id=None):
        item = TimeEntryApprovalItem.objects.get(id=item_id)
        approval = item.approval

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)

        item.approved = False
        item.comments = request.data.get("reason", "")
        item.save()

        e = item.time_entry
        e.is_locked = False
        e.save()

        return Response({"message": "Entry rejected."})

    # ========== DETAIL VIEW WITH DAILY BREAKDOWN ==========
    def retrieve(self, request, pk=None):
        approval = self.get_object()
        items = approval.items.select_related("time_entry__project")

        from collections import defaultdict

        # 🌐 Overall summary + per-(date, project) breakdown.
        # RT/OT logic is policy-aware (standard vs envision) inside the helper.
        summary = calculate_rt_ot_and_cost(items, workspace=approval.workspace)

        # 📆 Regroup breakdown by date for the response
        days = defaultdict(list)
        for (date, project_name), data in summary["breakdown"].items():
            days[date].append({
                "project":     project_name,
                "hours_total": round(float(data["total"]), 2),
                "rt_hours":    round(float(data["rt"]),    2),
                "ot_hours":    round(float(data["ot"]),    2),
                "cost":        round(float(data["cost"]),  2),
            })

        formatted_days = [
            {"date": str(date), "projects": projects}
            for date, projects in sorted(days.items())
        ]

        # 📤 Final Response JSON
        return Response({
            "id": approval.id,
            "employee": {
                "id": approval.user.id,
                "name": approval.user.get_full_name(),
            },
            "period": {
                "start_date": approval.start_date,
                "end_date": approval.end_date,
                "type": "day" if approval.start_date == approval.end_date else "week",
            },
            "summary": {
                "total_hours": summary["total_hours"],
                "regular_hours": summary["rt_hours"],
                "overtime_hours": summary["ot_hours"],
                "total_cost": summary["total_cost"]
            },
            "daily_breakdown": formatted_days,
            "status": approval.status,
            "can_approve": can_approve(request.user, approval.user, approval.workspace)
        })
