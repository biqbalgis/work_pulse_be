from rest_framework import viewsets, permissions, serializers
from rest_framework.exceptions import ValidationError

from .models import Project, ProjectRole, UserProjectRole, JobTitle
from .serializers import ProjectSerializer, ProjectRoleSerializer, UserProjectRoleSerializer, JobTitleSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser

def get_user_workspace(request):
    member = WorkspaceMember.objects.filter(user=request.user).first()
    return member.workspace if member else None

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

class JobTitleViewSet(viewsets.ModelViewSet):
    queryset = JobTitle.objects.all()
    serializer_class = JobTitleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(self.request.user, "CREATE", "JobTitle", instance.id, request=self.request)


class ProjectRoleViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectRoleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        workspace = get_user_workspace(self.request)
        return ProjectRole.objects.filter(project__workspace=workspace)

    def perform_create(self, serializer):
        project_role = serializer.save()
        log_activity(self.request.user, "CREATE", "ProjectRole", project_role.id, request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "ProjectRole", instance.id, request=self.request)
        instance.delete()


class UserProjectRoleViewSet(viewsets.ModelViewSet):
    serializer_class = UserProjectRoleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        workspace = get_user_workspace(self.request)
        return UserProjectRole.objects.filter(project__workspace=workspace)

    def perform_create(self, serializer):
        user = serializer.validated_data.get("user")
        project = serializer.validated_data.get("project")

        # Ensure user belongs to same workspace
        if not WorkspaceMember.objects.filter(user=user, workspace=project.workspace).exists():
            raise ValidationError("User does not belong to this workspace.")

        upr = serializer.save()
        log_activity(self.request.user, "CREATE", "UserProjectRole", upr.id, request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "UserProjectRole", instance.id, request=self.request)
        instance.delete()