from rest_framework import viewsets, permissions
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity

class WorkspaceViewSet(viewsets.ModelViewSet):
    queryset = Workspace.objects.all().order_by('-created_at')
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Workspace.objects.all().order_by('-created_at')
        return Workspace.objects.filter(members__user=user).order_by('-created_at')

        return queryset.none()
    def perform_create(self, serializer):
        user = self.request.user
        workspace = serializer.save(created_by=user)

        # Automatically make the creator an admin
        WorkspaceMember.objects.create(
            workspace=workspace,
            user=user,
            role='admin'
        )
        log_activity(user, "CREATE", "Workspace", workspace.id, request=self.request)
        return workspace

class WorkspaceMemberViewSet(viewsets.ModelViewSet):
    queryset = WorkspaceMember.objects.all()
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
