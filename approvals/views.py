from datetime import datetime

from django.db.models import Case, CharField, F, Prefetch, Value, When, prefetch_related_objects
from rest_framework import filters, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.utils.pagination import StandardPagination
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceUser
from .models import TimeEntryApproval, TimeEntryApprovalItem
from .serializers import TimeEntryApprovalSerializer, TimeEntryApprovalItemSerializer
from time_entries.models import TimeEntry
from .utils import can_approve, calculate_rt_ot_and_cost
from .review import APPROVED, REJECTED, decide_item, refresh_week_status, week_detail


class ApprovalPagination(StandardPagination):
    # Members fetch their own full history in one shot (no page picker on that
    # side), so this needs a much higher ceiling than the standard page cap.
    max_page_size = 1000


class TimeEntryApprovalViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntryApprovalSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    pagination_class = ApprovalPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'status']
    ordering_fields = ['user__first_name', 'start_date', 'total_hours', 'status', 'entry_type']

    def get_queryset(self):
        user = self.request.user
        queryset = TimeEntryApproval.objects.filter(is_deleted=False).select_related("user", "workspace").annotate(
            # "day" vs "week" has no stored column — it's derived from whether
            # start_date == end_date — so annotate it to make the Type column sortable.
            entry_type=Case(
                When(start_date=F('end_date'), then=Value('day')),
                default=Value('week'),
                output_field=CharField(),
            )
        )

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

    def _approver_context(self, user, approvals):
        """The caller's and the employees' memberships for the whole page in two queries, for can_approve."""
        if user.is_superuser:
            return {"superuser": True}
        workspace_ids = {a.workspace_id for a in approvals}
        user_ids = {a.user_id for a in approvals}
        return {
            "superuser": False,
            "approvers": {m.workspace_id: m for m in WorkspaceMember.objects.filter(user=user, workspace_id__in=workspace_ids)},
            "employees": {
                (m.user_id, m.workspace_id): m
                for m in WorkspaceMember.objects.filter(user_id__in=user_ids, workspace_id__in=workspace_ids)
            },
        }

    @staticmethod
    def _can_approve_from_context(context, request_user, approval):
        """Same rules as approvals.utils.can_approve, answered from the preloaded memberships."""
        if context["superuser"]:
            return True
        approver = context["approvers"].get(approval.workspace_id)
        employee = context["employees"].get((approval.user_id, approval.workspace_id))
        if not approver or not employee:
            return False
        if employee.manager_id:
            return employee.manager_id == request_user.id or approver.role == "admin"
        return approver.role == "admin"

    def _serialize_approval_summary(self, approval, request, context=None):
        if context is None:
            items = approval.items.select_related("time_entry__project")
            entries_count = items.count()
        else:
            items = list(approval.items.all())  # prefetched by list()
            entries_count = len(items)
        metrics = calculate_rt_ot_and_cost(items, workspace=approval.workspace)
        # Equipment and rate charges on the week's billable entries.
        lines_total = round(float(sum(
            usage.cost or 0
            for item in items if item.time_entry.is_billable_to_client
            for usage in item.time_entry.asset_usages.all()
        )), 2)

        return {
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
            "entries_count": entries_count,
            "item_counts": {
                status: sum(1 for item in items if item.status == status)
                for status in ("pending", "approved", "rejected")
            },
            "hours": {
                "total": metrics["total_hours"],
                "regular": metrics["rt_hours"],
                "overtime": metrics["ot_hours"]
            },
            "cost": {
                "total": metrics["total_cost"],
                "lines": lines_total,
                "grand_total": round(metrics["total_cost"] + lines_total, 2),
            },
            "status": approval.status,
            "can_approve": (
                can_approve(request.user, approval.user, approval.workspace)
                if context is None
                else self._can_approve_from_context(context, request.user, approval)
            ),
        }

    # ========== EMPLOYEE SUBMITS WEEK ==========
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        approvals = [a for a in (page if page is not None else queryset)]

        # One query for all the page's items (with their entry + project) and two for the memberships,
        # instead of ~3 queries per approval.
        prefetch_related_objects(
            approvals,
            Prefetch(
                "items",
                queryset=TimeEntryApprovalItem.objects.filter(is_deleted=False)
                .select_related("time_entry__project")
                .prefetch_related("time_entry__asset_usages"),
            ),
        )
        context = self._approver_context(request.user, approvals)
        data = [self._serialize_approval_summary(approval, request, context) for approval in approvals]

        if page is not None:
            return self.get_paginated_response(data)
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
        """Approves (and locks) every entry still waiting. Entries rejected one by one stay rejected until the
        employee resubmits them, so the week is only marked approved once none are left."""
        approval = self.get_object()

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "You are not authorized to approve this employee's time."}, status=403)

        items = approval.items.filter(is_deleted=False).select_related("time_entry")
        for item in items:
            if item.status != REJECTED:
                decide_item(item, APPROVED, request.user)
        refresh_week_status(approval, request.user)

        rejected = sum(1 for item in items if item.status == REJECTED)
        if rejected:
            return Response({"message": f"Week approved except {rejected} rejected "
                                        f"{'entry' if rejected == 1 else 'entries'} waiting for the employee."})
        return Response({"message": "Week approved successfully."})

    # ========== REJECT WEEK ==========
    @action(detail=True, methods=["post"], url_path="reject-week")
    def reject_week(self, request, pk=None):
        """Sends the week back: every entry not yet approved is rejected with the reason and unlocked so the
        employee can fix and resubmit it."""
        approval = self.get_object()

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "You are not authorized to approve this employee's time."}, status=403)
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response({"error": "Give a reason so the employee knows what to fix."}, status=400)

        for item in approval.items.filter(is_deleted=False).select_related("time_entry"):
            ticket = item.time_entry.field_ticket()
            if item.status != APPROVED and not (ticket and ticket.status == "invoiced"):
                decide_item(item, REJECTED, request.user, reason)
        approval.status = "rejected"
        approval.notes = reason
        approval.reviewed_by = request.user
        approval.save(update_fields=["status", "notes", "reviewed_by"])

        return Response({"message": "Week rejected. The employee can fix the entries and resubmit them."})

    # ========== APPROVE / REJECT SINGLE ENTRY ==========
    def _item_for(self, pk, item_id):
        return (
            TimeEntryApprovalItem.objects.filter(id=item_id, approval_id=pk, is_deleted=False)
            .select_related("approval__user", "approval__workspace", "time_entry")
            .first()
        )

    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/approve")
    def approve_entry(self, request, pk=None, item_id=None):
        item = self._item_for(pk, item_id)
        if not item:
            return Response({"error": "Entry not found."}, status=404)
        approval = item.approval

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)

        decide_item(item, APPROVED, request.user)
        refresh_week_status(approval, request.user)
        return Response({"message": "Entry approved.", "week_status": approval.status})

    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/reject")
    def reject_entry(self, request, pk=None, item_id=None):
        item = self._item_for(pk, item_id)
        if not item:
            return Response({"error": "Entry not found."}, status=404)
        approval = item.approval

        if not can_approve(request.user, approval.user, approval.workspace):
            return Response({"error": "Unauthorized"}, status=403)
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response({"error": "Give a reason so the employee knows what to fix."}, status=400)
        ticket = item.time_entry.field_ticket()
        if ticket and ticket.status == "invoiced":
            return Response({"error": f"This time is on invoiced field ticket {ticket.ticket_number} and can't be sent back."}, status=400)

        decide_item(item, REJECTED, request.user, reason)
        refresh_week_status(approval, request.user)
        return Response({"message": "Entry rejected. The employee can fix it and resubmit.", "week_status": approval.status})

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
            **week_detail(approval),
            "notes": approval.notes,
            "reviewed_by": approval.reviewed_by.get_full_name() if approval.reviewed_by else "",
            "status": approval.status,
            "can_approve": can_approve(request.user, approval.user, approval.workspace)
        })
