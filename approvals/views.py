from datetime import datetime

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils.timezone import now

from workspaces.permissions import IsWorkspaceUser
from .models import TimeEntryApproval, TimeEntryApprovalItem
from .serializers import TimeEntryApprovalSerializer, TimeEntryApprovalItemSerializer
from time_entries.models import TimeEntry
from .utils import can_approve


class TimeEntryApprovalViewSet(viewsets.ModelViewSet):
    queryset = TimeEntryApproval.objects.all()
    serializer_class = TimeEntryApprovalSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    # ========== EMPLOYEE SUBMITS WEEK ==========
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
    @action(detail=True, methods=["post"], url_path="reject-week")
    def reject_week(self, request, pk=None):
        approval = self.get_object()

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)

        reason = request.data.get("reason", "")
        approval.status = "rejected"
        approval.notes = reason
        approval.reviewed_by = request.user
        approval.save()

        # Unlock entries for editing
        for item in approval.items.all():
            e = item.time_entry
            e.is_locked = False
            e.save()

        return Response({"message": "Week rejected."})

    # ========== APPROVE / REJECT SINGLE ENTRY ==========
    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/approve")
    def approve_entry(self, request, pk=None, item_id=None):
        item = TimeEntryApprovalItem.objects.get(id=item_id)
        approval = item.approval

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)

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
