from rest_framework.response import Response
from rest_framework import viewsets, permissions, serializers,status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from .models import Project, ProjectRole, UserProjectRole, JobTitle
from .serializers import ProjectSerializer, ProjectRoleSerializer, UserProjectRoleSerializer, JobTitleSerializer, \
    AddProjectRoleSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser, IsWorkspaceAdminOrSuperUser


def get_user_workspace(request):
    member = WorkspaceMember.objects.filter(user=request.user).first()
    return member.workspace if member else None

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)
    # permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdminOrSuperUser]

    def get_queryset(self):
        user = self.request.user
        queryset = Project.objects.filter(is_deleted=False)

        if not user.is_superuser:
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
            queryset = queryset.filter(workspace_id__in=workspace_ids)

        # Apply filters
        workspace_id = self.request.query_params.get('workspace')
        client_id = self.request.query_params.get('client')

        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)
        
        if client_id:
            queryset = queryset.filter(client=client_id)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser:
            workspace = serializer.validated_data.get("workspace")
            if workspace is None:
                raise serializers.ValidationError({"workspace": "Workspace is required for superuser."})

            # NON-SUPERUSER: workspace MUST NOT come from payload. Use membership.
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
        queryset = ProjectRole.objects.filter(project__workspace=workspace)

        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        return queryset

    def perform_create(self, serializer):
        project_role = serializer.save()
        log_activity(self.request.user, "CREATE", "ProjectRole", project_role.id, request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "ProjectRole", instance.id, request=self.request)
        instance.delete()

    @action(detail=False, methods=['post'], url_path='add-project-role')
    def add_project_role(self, request):
        serializer = AddProjectRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        project_id = serializer.validated_data["project"]
        job_title_name = serializer.validated_data["job_title_name"].strip()
        hourly_rate = serializer.validated_data["hourly_rate"]
        user = request.user

        # Workspace: ensure user belongs to the workspace of project
        try:
            project = Project.objects.get(id=project_id, is_deleted=False)
        except Project.DoesNotExist:
            raise ValidationError("Invalid project ID.")

        if not WorkspaceMember.objects.filter(user=user, workspace=project.workspace).exists():
            raise ValidationError("You do not belong to this workspace.")

        # Step 1: Create or get job title
        job_title, created = JobTitle.objects.get_or_create(
            name__iexact=job_title_name,
            defaults={'name': job_title_name}
        )

        # Step 2: Create or update ProjectRole
        project_role, pr_created = ProjectRole.objects.get_or_create(
            project=project,
            job_title=job_title,
            defaults={"hourly_rate": hourly_rate}
        )

        # Update rate if already exists
        if not pr_created:
            project_role.hourly_rate = hourly_rate
            project_role.save()

        # Logging
        log_activity(
            user,
            "CREATE" if pr_created else "UPDATE",
            "ProjectRole",
            project_role.id,
            request=request
        )

        # Final response
        return Response({"project_role": ProjectRoleSerializer(project_role).data,"job_title_created": created,"project_role_created": pr_created},status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="assign-user-role")
    def assign_user_role(self, request):
        project_id = request.data.get("project")
        user_id = request.data.get("user")
        job_title_id = request.data.get("job_title")
        hourly_rate = request.data.get("hourly_rate")

        if not project_id or not user_id or not job_title_id:
            raise ValidationError("project, user and job_title are required.")

        # Ensure job_title exists for the project
        project_role = ProjectRole.objects.filter(
            project_id=project_id,
            job_title_id=job_title_id
        ).first()

        if not project_role:
            raise ValidationError("This job title is not configured for this project.")

        # Create or update UserProjectRole
        upr, created = UserProjectRole.objects.update_or_create(
            project_id=project_id,
            user_id=user_id,
            job_title_id=job_title_id,
            defaults={"hourly_rate": hourly_rate or project_role.hourly_rate}
        )

        return Response(
            {
                "status": "success",
                "created": created,
                "user_project_role": UserProjectRoleSerializer(upr).data
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=False, methods=['post'], url_path='user-job-titles')
    def get_user_job_titles(self, request):
        project_id = request.data.get("project")
        user_id = request.data.get("user")

        if not project_id or not user_id:
            return Response(
                {"error": "project_id and user_id are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        roles = UserProjectRole.objects.filter(
            project_id=project_id,
            user_id=user_id
        ).select_related("job_title")

        data = [
            {
                "job_title_id": str(role.job_title.id),
                "job_title": role.job_title.name,
                "hourly_rate": role.hourly_rate,
            }
            for role in roles
        ]

        return Response(data, status=status.HTTP_200_OK)

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


