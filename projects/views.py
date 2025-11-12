from rest_framework import viewsets, permissions, serializers
from .models import Project
from .serializers import ProjectSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Project.objects.filter(is_deleted=False)
        workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
        return Project.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser and 'workspace' in self.request.data:
            workspace = serializer.validated_data.get('workspace')
        else:
            member = WorkspaceMember.objects.filter(user=user).first()
            if not member:
                raise serializers.ValidationError("User is not a member of any workspace.")
            workspace = member.workspace
        instance = serializer.save(workspace=workspace, created_by=user)
        log_activity(user, "CREATE", "Project", instance.id, request=self.request)


    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Project", instance.id, request=self.request)
        instance.delete()
