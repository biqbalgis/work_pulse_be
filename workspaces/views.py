from rest_framework import viewsets, permissions
from django.db.models import Prefetch
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity


class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        base_queryset = Workspace.objects.all().prefetch_related(
            Prefetch('members', queryset=WorkspaceMember.objects.select_related('user'))
        ).order_by('-created_at')

        user = self.request.user
        if user.is_superuser:
            return base_queryset
        return base_queryset.filter(members__user=user)

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
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
    def get_queryset(self):
        workspace_id = self.request.query_params.get("workspace", None)

        # Superuser sees all
        if self.request.user.is_superuser:
            qs = WorkspaceMember.objects.all()

        else:
            # Normal users see only their workspace
            qs = WorkspaceMember.objects.filter(workspace__members__user=self.request.user)

        # Filter for specific workspace if provided
        if workspace_id:
            qs = qs.filter(workspace_id=workspace_id)

        return qs.select_related("user", "workspace")
