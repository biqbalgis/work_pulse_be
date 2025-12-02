from rest_framework import viewsets, permissions, serializers
from .models import TimeOffType, TimeOffRequest
from .serializers import TimeOffTypeSerializer, TimeOffRequestSerializer
from workspaces.permissions import IsWorkspaceUser
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity

class TimeOffTypeViewSet(viewsets.ModelViewSet):
    serializer_class = TimeOffTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceUser]

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
        instance = serializer.save(workspace=workspace)
        log_activity(self.request.user, "CREATE", "TimeOffType", instance.id,request=self.request)


class TimeOffRequestViewSet(viewsets.ModelViewSet):
    serializer_class = TimeOffRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceUser]

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        return TimeOffRequest.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        workspace = get_user_primary_workspace(self.request.user)
        instance = serializer.save(workspace=workspace, user=self.request.user)
        log_activity(self.request.user, "CREATE", "TimeOffRequest", instance.id,request=self.request)
