from rest_framework import viewsets, permissions, serializers
from .models import Task
from .serializers import TaskSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser, IsWorkspaceAdminOrSuperUser


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    # permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdminOrSuperUser]

    def get_queryset(self):
        queryset = Task.objects.filter(is_deleted=False, is_active=True)
        user = self.request.user

        # Detail actions (retrieve/update/partial_update/destroy) — the task
        # is already identified by its PK in the URL, so requiring
        # ?project=/?workspace= query params here (as the list action does)
        # would make GET/PUT/PATCH/DELETE by id impossible. Scope by
        # workspace membership only.
        if self.action != 'list':
            if user.is_superuser:
                return queryset
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
            return queryset.filter(project__workspace_id__in=workspace_ids)

        workspace_id = self.request.query_params.get('workspace')
        project_id = self.request.query_params.get('project')

        if user.is_superuser:
            if workspace_id and project_id:
                return queryset.filter(project__workspace_id=workspace_id, project_id=project_id)
            return Task.objects.none()

        # For regular users
        if project_id:
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
            return queryset.filter(project_id=project_id, project__workspace_id__in=workspace_ids)

        return Task.objects.none()

    def list(self, request, *args, **kwargs):
        if request.query_params.get('pagination') == 'false':
            self.pagination_class = None
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        instance = serializer.save()
        log_activity(user, "CREATE", "Task", instance.id,request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(self.request.user, "UPDATE", "Task", instance.id, request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Task", instance.id,request=self.request)
        instance.delete()
