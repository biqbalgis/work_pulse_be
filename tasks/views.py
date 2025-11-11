from rest_framework import viewsets, permissions, serializers
from .models import Task
from .serializers import TaskSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser

class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Task.objects.filter(is_deleted=False)
        workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
        return Task.objects.filter(project__workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        user = self.request.user
        instance = serializer.save()
        log_activity(user, "CREATE", "Task", instance.id)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Task", instance.id)
        instance.delete()
