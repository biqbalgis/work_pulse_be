from rest_framework import viewsets, permissions, serializers
from .models import TimeEntry
from .serializers import TimeEntrySerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceUser, IsSuperUser

class TimeEntryViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceUser | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return TimeEntry.objects.filter(is_deleted=False)
        workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
        return TimeEntry.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        user = self.request.user
        member = WorkspaceMember.objects.filter(user=user).first()
        if not member:
            raise serializers.ValidationError("User is not part of any workspace.")
        instance = serializer.save(user=user, workspace=member.workspace)
        log_activity(user, "CREATE", "TimeEntry", instance.id,request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "TimeEntry", instance.id,request=self.request)
        instance.delete()
