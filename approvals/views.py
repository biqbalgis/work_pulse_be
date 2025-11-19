from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.timezone import now
from .models import TimeEntryApproval, TimeEntryApprovalItem
from .serializers import TimeEntryApprovalSerializer, TimeEntryApprovalItemSerializer
from time_entries.models import TimeEntry


class TimeEntryApprovalViewSet(viewsets.ModelViewSet):
    queryset = TimeEntryApproval.objects.all()
    serializer_class = TimeEntryApprovalSerializer

    # ========= EMPLOYEE SUBMITS WEEK =========
    @action(detail=False, methods=["post"], url_path="submit-week")
    def submit_week(self, request):
        user = request.user
        start_date = request.data["start_date"]
        end_date = request.data["end_date"]

        # Get all employee's time entries for that week
        entries = TimeEntry.objects.filter(
            user=user,
            start_time__date__range=[start_date, end_date],
        )

        if not entries.exists():
            return Response({"error": "No time entries for this period"}, status=400)

        approval = TimeEntryApproval.objects.create(
            workspace=user.workspace,
            user=user,
            start_date=start_date,
            end_date=end_date,
            status="submitted",
            created_by=user,
        )

        # Create Approval Items (one per time entry)
        for entry in entries:
            TimeEntryApprovalItem.objects.create(
                approval=approval,
                time_entry=entry,
                approved=True,
                created_by=user
            )

        return Response({"status": "submitted", "approval_id": approval.id}, status=200)

    # ========= APPROVE WHOLE WEEK =========
    @action(detail=True, methods=["post"], url_path="approve-all")
    def approve_all(self, request, pk=None):
        approval = self.get_object()

        # Approve each item
        for item in approval.items.all():
            item.approved = True
            item.save()

            # Lock corresponding TimeEntry
            entry = item.time_entry
            entry.is_locked = True
            entry.save()

        approval.status = "approved"
        approval.reviewed_by = request.user
        approval.save()

        return Response({"status": "week approved"}, status=200)

    # ========= REJECT WHOLE WEEK =========
    @action(detail=True, methods=["post"], url_path="reject-week")
    def reject_week(self, request, pk=None):
        approval = self.get_object()
        reason = request.data.get("reason", "")

        approval.status = "rejected"
        approval.notes = reason
        approval.reviewed_by = request.user
        approval.save()

        # Unlock all time entries to allow employee edits
        for item in approval.items.all():
            entry = item.time_entry
            entry.is_locked = False
            entry.save()

        return Response({"status": "week rejected"}, status=200)

class TimeEntryApprovalItemViewSet(viewsets.ModelViewSet):
    queryset = TimeEntryApprovalItem.objects.all()
    serializer_class = TimeEntryApprovalItemSerializer

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        item = self.get_object()
        item.approved = True
        item.save()

        # Lock entry
        entry = item.time_entry
        entry.is_locked = True
        entry.save()

        return Response({"status": "approved"}, status=200)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        item = self.get_object()
        item.approved = False
        item.comments = request.data.get("reason", "")
        item.save()

        # Unlock entry so employee can fix
        entry = item.time_entry
        entry.is_locked = False
        entry.save()

        return Response({"status": "rejected"}, status=200)
