from rest_framework import viewsets, permissions
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity

class WorkspaceViewSet(viewsets.ModelViewSet):
    queryset = Workspace.objects.filter(is_deleted=False)
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_activity(self.request.user, "CREATE", "Workspace", instance.id)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Workspace", instance.id)
        instance.delete()

class WorkspaceMemberViewSet(viewsets.ModelViewSet):
    queryset = WorkspaceMember.objects.all()
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
