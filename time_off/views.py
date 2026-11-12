"""
Time off.

Types: every member reads them; adding / changing / removing them needs "Manage time-off types".
Requests: everyone sees and changes their own (while pending); "View everyone's time off" shows the whole
workspace; approving / rejecting needs "Approve / reject time off" (nobody decides on their own request, except
administrators).
"""

from django.db.models import Q
from django.utils import timezone
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from core.utils.logger import log_activity
from core.utils.workspace_utils import get_user_primary_workspace, get_user_workspace_ids
from user_permissions.access import has_perm, workspaces_with
from user_permissions.drf import ActionPermissions, require
from workspaces.permissions import IsWorkspaceUser

from .models import TimeOffRequest, TimeOffType
from .serializers import TimeOffRequestSerializer, TimeOffTypeSerializer

PENDING = "pending"


class TimeOffTypeViewSet(viewsets.ModelViewSet):
    serializer_class = TimeOffTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceUser, ActionPermissions]
    action_permissions = {
        "update": "time_off.manage_types",
        "partial_update": "time_off.manage_types",
        "destroy": "time_off.manage_types",
    }
    action_permission_messages = {key: "You don't have permission to manage time-off types." for key in action_permissions}

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        return TimeOffType.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        workspace = get_user_primary_workspace(self.request.user)
        require(self.request.user, workspace.id if workspace else None, "time_off.manage_types",
                "You don't have permission to manage time-off types.")
        instance = serializer.save(workspace=workspace)
        log_activity(self.request.user, "CREATE", "TimeOffType", instance.id, request=self.request)


class TimeOffRequestViewSet(viewsets.ModelViewSet):
    serializer_class = TimeOffRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__first_name', 'user__last_name', 'type__name', 'status', 'reason']
    ordering_fields = ['user__first_name', 'start_date', 'total_days', 'type__name', 'status']

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        queryset = TimeOffRequest.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)
        if not user.is_superuser:
            # Their own requests, plus everyone's where they may view or decide on them.
            others = (workspaces_with(user, "time_off.view_all") or set()) | (workspaces_with(user, "time_off.approve") or set())
            queryset = queryset.filter(Q(user=user) | Q(workspace_id__in=others))
        # The type and employee names print on every row: load them with the requests, not one query per row.
        return queryset.select_related("type", "user")

    def perform_create(self, serializer):
        workspace = get_user_primary_workspace(self.request.user)
        require(self.request.user, workspace.id if workspace else None, "time_off.view",
                "You don't have permission to request time off.")
        instance = serializer.save(workspace=workspace, user=self.request.user, status=PENDING)
        log_activity(self.request.user, "CREATE", "TimeOffRequest", instance.id, request=self.request)

    def perform_update(self, serializer):
        request = serializer.instance
        if request.user_id != self.request.user.id and not self.request.user.is_superuser:
            raise PermissionDenied("You can only change your own time-off requests.")
        if request.status != PENDING:
            raise ValidationError("This request has already been decided and can't be changed.")
        serializer.save()

    def perform_destroy(self, instance):
        actor = self.request.user
        own_pending = instance.user_id == actor.id and instance.status == PENDING
        if not (actor.is_superuser or own_pending or has_perm(actor, instance.workspace_id, "time_off.approve")):
            raise PermissionDenied("You can only remove your own pending time-off requests.")
        instance.delete()

    def _decide(self, request, status, comment=""):
        time_off = self.get_object()
        actor = request.user
        if not actor.is_superuser:
            require(actor, time_off.workspace_id, "time_off.approve", "You don't have permission to approve time off.")
            if time_off.user_id == actor.id and not has_perm(actor, time_off.workspace_id, "team.manage_permissions"):
                raise PermissionDenied("Ask someone else to decide on your own time off.")
        if time_off.status != PENDING:
            raise ValidationError(f"This request is already {time_off.status}.")
        time_off.status = status
        time_off.reviewed_by = actor
        time_off.reviewer_comment = comment
        time_off.save(update_fields=["status", "reviewed_by", "reviewer_comment"])
        log_activity(actor, status.upper(), "TimeOffRequest", time_off.id, request=request)
        return Response(self.get_serializer(time_off).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, "approved", str(request.data.get("comment") or "").strip())

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("Give a reason so the employee knows why.")
        return self._decide(request, "rejected", reason)
