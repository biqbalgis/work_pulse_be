from rest_framework import viewsets, permissions
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity

class WorkspaceViewSet(viewsets.ModelViewSet):
    queryset = Workspace.objects.all()
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        user = self.request.user
        workspace = serializer.save(created_by=user)
        # Make this user the admin of the new workspace
        WorkspaceMember.objects.create(workspace=workspace, user=user, role='admin')
        user.primary_workspace = workspace
        user.save(update_fields=['primary_workspace'])
        log_activity(user, "CREATE", "Workspace", workspace.id, request=self.request)

class WorkspaceMemberViewSet(viewsets.ModelViewSet):
    queryset = WorkspaceMember.objects.all()
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
