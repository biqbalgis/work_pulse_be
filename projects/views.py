from rest_framework.response import Response
from rest_framework import viewsets, permissions, serializers,status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from django.db import transaction

from .models import Project, ProjectRole, UserProjectRole, JobTitle, RoleTemplate, RoleTemplateUser
from .utils import get_next_job_code
from .serializers import (
    ProjectSerializer,
    ProjectRoleSerializer,
    UserProjectRoleSerializer,
    JobTitleSerializer,
    AddProjectRoleSerializer,
    AssignProjectRoleUsersSerializer,
    CurrentUserRoleSerializer,
    RoleTemplateCreateSerializer,
    RoleTemplateOutputSerializer,
)
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser, IsWorkspaceAdminOrSuperUser
from users.models import User


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
        # Base queryset: only active projects and not deleted
        queryset = Project.objects.filter(is_deleted=False, is_active=True)

        if user.is_superuser:
            # Superuser MUST provide workspace parameter
            workspace_id = self.request.query_params.get('workspace')
            if not workspace_id:
                raise ValidationError({"workspace": "Workspace parameter is required for superusers."})
            queryset = queryset.filter(workspace_id=workspace_id)
        else:
            # Non-superuser: filter by their workspace membership
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
            queryset = queryset.filter(workspace_id__in=workspace_ids)
            
            # Optional: allow further filtering if they belong to multiple workspaces (though usually 1)
            workspace_id = self.request.query_params.get('workspace')
            if workspace_id:
                queryset = queryset.filter(workspace_id=workspace_id)

        # Apply other filters
        client_id = self.request.query_params.get('client')
        if client_id:
            queryset = queryset.filter(client=client_id)

        job_code = self.request.query_params.get('job_code')
        if job_code:
            queryset = queryset.filter(job_code__icontains=job_code)

        name = self.request.query_params.get('name')
        if name:
            queryset = queryset.filter(name__icontains=name)

        return queryset

    def perform_create(self, serializer):
        from workspaces.models import Workspace

        user = self.request.user
        if user.is_superuser:
            # workspace is read-only in the serializer, so read it directly from request.data
            workspace_id = self.request.data.get("workspace")
            if not workspace_id:
                raise serializers.ValidationError({"workspace": "Workspace is required for superuser."})
            try:
                workspace = Workspace.objects.get(id=workspace_id)
            except Workspace.DoesNotExist:
                raise serializers.ValidationError({"workspace": "Workspace not found."})
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

    @action(detail=False, methods=["get"], url_path="next-job-number")
    def next_job_number(self, request):
        """
        GET /api/projects/next-job-number/
        Returns the next job number for the caller's workspace without saving it.
        Superusers must pass ?workspace=<id>.
        """
        if request.user.is_superuser:
            workspace_id = request.query_params.get("workspace")
            if not workspace_id:
                return Response({"error": "workspace query param is required for superusers."}, status=400)
            from workspaces.models import Workspace
            try:
                workspace = Workspace.objects.get(id=workspace_id)
            except Workspace.DoesNotExist:
                return Response({"error": "Workspace not found."}, status=404)
        else:
            member = WorkspaceMember.objects.filter(user=request.user).first()
            if not member:
                return Response({"error": "You are not a member of any workspace."}, status=400)
            workspace = member.workspace

        return Response({"next_job_number": get_next_job_code(workspace)})

class JobTitleViewSet(viewsets.ModelViewSet):
    queryset = JobTitle.objects.filter(is_deleted=False)
    serializer_class = JobTitleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(self.request.user, "CREATE", "JobTitle", instance.id, request=self.request)


class ProjectRoleViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectRoleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        workspace = get_user_workspace(self.request)
        queryset = ProjectRole.objects.filter(project__workspace=workspace, is_deleted=False)

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

    def _get_project_for_request(self, request, project_id):
        try:
            project = Project.objects.get(id=project_id, is_deleted=False)
        except Project.DoesNotExist:
            raise ValidationError("Invalid project ID.")

        if not request.user.is_superuser:
            if not WorkspaceMember.objects.filter(user=request.user, workspace=project.workspace).exists():
                raise ValidationError("You do not belong to this workspace.")

        return project

    def _get_or_create_job_title(self, job_title_name):
        job_title = JobTitle.objects.filter(name__iexact=job_title_name).first()
        if job_title:
            return job_title, False

        return JobTitle.objects.create(name=job_title_name), True

    def _get_or_create_project_role(self, project, job_title, hourly_rate):
        project_role, created = ProjectRole.objects.get_or_create(
            project=project,
            job_title=job_title,
            defaults={"hourly_rate": hourly_rate}
        )

        if not created and project_role.hourly_rate != hourly_rate:
            project_role.hourly_rate = hourly_rate
            project_role.save(update_fields=["hourly_rate"])

        return project_role, created

    def _validate_project_users(self, project, requested_user_ids):
        existing_user_ids = set(User.objects.filter(id__in=requested_user_ids).values_list("id", flat=True))
        missing_user_ids = [str(user_id) for user_id in requested_user_ids if user_id not in existing_user_ids]
        if missing_user_ids:
            raise ValidationError({"users": f"Invalid user ids: {', '.join(missing_user_ids)}"})

        member_user_ids = set(
            WorkspaceMember.objects.filter(
                workspace=project.workspace,
                user_id__in=requested_user_ids
            ).values_list("user_id", flat=True)
        )
        missing_member_ids = [str(user_id) for user_id in requested_user_ids if user_id not in member_user_ids]
        if missing_member_ids:
            raise ValidationError(
                {"users": f"These users do not belong to the project workspace: {', '.join(missing_member_ids)}"}
            )

    @action(detail=False, methods=['post'], url_path='add-project-role')
    def add_project_role(self, request):
        serializer = AddProjectRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        project_id = serializer.validated_data["project"]
        job_title_name = serializer.validated_data["job_title_name"].strip()
        hourly_rate = serializer.validated_data["hourly_rate"]
        user = request.user

        project = self._get_project_for_request(request, project_id)

        # Step 1: Create or get job title
        job_title, created = self._get_or_create_job_title(job_title_name)

        # Step 2: Create or update ProjectRole
        project_role, pr_created = self._get_or_create_project_role(project, job_title, hourly_rate)

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

    @action(detail=False, methods=["post"], url_path="assign-role-users")
    def assign_role_users(self, request):
        serializer = AssignProjectRoleUsersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        project = self._get_project_for_request(request, serializer.validated_data["project"])
        job_title_name = serializer.validated_data["job_title_name"].strip()
        hourly_rate = serializer.validated_data["hourly_rate"]
        user_ids = serializer.validated_data["users"]
        self._validate_project_users(project, user_ids)

        with transaction.atomic():
            job_title, job_title_created = self._get_or_create_job_title(job_title_name)
            project_role, project_role_created = self._get_or_create_project_role(
                project,
                job_title,
                hourly_rate
            )

            log_activity(
                request.user,
                "CREATE" if project_role_created else "UPDATE",
                "ProjectRole",
                project_role.id,
                request=request
            )

            assigned_users = []
            created_any = job_title_created or project_role_created
            for user_id in user_ids:
                user_project_role, user_role_created = UserProjectRole.objects.update_or_create(
                    project=project,
                    user_id=user_id,
                    job_title=job_title,
                    defaults={"hourly_rate": hourly_rate}
                )

                log_activity(
                    request.user,
                    "CREATE" if user_role_created else "UPDATE",
                    "UserProjectRole",
                    user_project_role.id,
                    request=request
                )

                assigned_users.append({
                    "created": user_role_created,
                    "user_project_role": UserProjectRoleSerializer(user_project_role).data,
                })
                created_any = created_any or user_role_created

        return Response(
            {
                "project": str(project.id),
                "job_title_created": job_title_created,
                "project_role_created": project_role_created,
                "project_role": ProjectRoleSerializer(project_role).data,
                "assigned_users": assigned_users,
            },
            status=status.HTTP_201_CREATED if created_any else status.HTTP_200_OK
        )

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
    pagination_class = None

    def get_queryset(self):
        workspace = get_user_workspace(self.request)
        queryset = (
            UserProjectRole.objects
            .select_related('user', 'project', 'job_title')
            .filter(project__workspace=workspace, is_deleted=False)
            .order_by('user__first_name')  # or whatever you prefer
        )

        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        return queryset

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



from rest_framework.views import APIView

class CurrentUserProjectRolesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        project_id = request.query_params.get("project_id")
        if not project_id:
            return Response({"error": "project_id query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        roles = UserProjectRole.objects.filter(
            user=request.user, 
            project_id=project_id,
            is_deleted=False).select_related("job_title")

        serializer = CurrentUserRoleSerializer(roles, many=True)
        return Response(serializer.data)


class RoleTemplateViewSet(viewsets.ViewSet):
    """
    POST /api/role-templates/       — create a new template
    GET  /api/role-templates/       — list all templates created by the current user
    DELETE /api/role-templates/{id}/ — soft-delete a template
    """
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request):
        serializer = RoleTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        job_title_id   = data['job_title_id']
        job_title_name = data['job_title_name'].strip()
        user_ids       = data['user_ids']

        # Resolve or create the job title
        job_title = JobTitle.objects.filter(id=job_title_id).first()
        if not job_title:
            # Fall back to name lookup, then create
            job_title, _ = JobTitle.objects.get_or_create(name=job_title_name)

        # Validate all user IDs exist
        User = request.user.__class__
        existing_ids = set(
            User.objects.filter(id__in=user_ids).values_list('id', flat=True)
        )
        missing = [str(uid) for uid in user_ids if uid not in existing_ids]
        if missing:
            return Response(
                {'error': f"Users not found: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            template = RoleTemplate.objects.create(
                template_name=data['template_name'],
                job_title=job_title,
                hourly_rate=data['hourly_rate'],
                created_by=request.user,
            )
            RoleTemplateUser.objects.bulk_create([
                RoleTemplateUser(template=template, user_id=uid)
                for uid in user_ids
            ])

        log_activity(request.user, 'CREATE', 'RoleTemplate', template.id, request=request)

        return Response(
            RoleTemplateOutputSerializer(template).data,
            status=status.HTTP_201_CREATED,
        )

    def list(self, request):
        templates = (
            RoleTemplate.objects
            .filter(created_by=request.user, is_deleted=False)
            .select_related('job_title', 'created_by')
            .prefetch_related('template_users__user')
            .order_by('-created_at')
        )
        return Response(RoleTemplateOutputSerializer(templates, many=True).data)

    def destroy(self, request, pk=None):
        template = RoleTemplate.objects.filter(id=pk, created_by=request.user, is_deleted=False).first()
        if not template:
            return Response({'error': 'Template not found.'}, status=status.HTTP_404_NOT_FOUND)
        template.delete()
        log_activity(request.user, 'DELETE', 'RoleTemplate', template.id, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
