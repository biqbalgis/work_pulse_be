from rest_framework import viewsets, permissions, serializers

from workspaces.permissions import IsWorkspaceManager
from .models import TimeEntryApproval, TimeEntryApprovalItem
from .serializers import TimeEntryApprovalSerializer
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity

class TimeEntryApprovalViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntryApprovalSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager]

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        return TimeEntryApproval.objects.filter(workspace_id__in=workspace_ids)

    def perform_create(self, serializer):
        user = self.request.user
        workspace = get_user_primary_workspace(user)
        instance = serializer.save(workspace=workspace, reviewed_by=user)
        log_activity(user, "CREATE", "TimeEntryApproval", instance.id, request=self.request)
