from rest_framework import viewsets, permissions
from rest_framework.exceptions import ValidationError
from .models import OrganizationAsset
from .serializers import OrganizationAssetSerializer
from workspaces.models import WorkspaceMember


class OrganizationAssetViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationAssetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            qs = OrganizationAsset.objects.filter(is_active=True, is_deleted=False)
        else:
            workspace_ids = WorkspaceMember.objects.filter(
                user=user
            ).values_list("workspace_id", flat=True)
            qs = OrganizationAsset.objects.filter(
                workspace_id__in=workspace_ids,
                is_active=True,
                is_deleted=False,
            )

        # Filter by project — used by field tickets and time entry
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)

        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data.get("project")

        if project:
            workspace = project.workspace
        else:
            wm = WorkspaceMember.objects.filter(user=user).first()
            if not wm:
                raise ValidationError("You are not part of any workspace.")
            workspace = wm.workspace

        serializer.save(workspace=workspace)

    def perform_update(self, serializer):
        instance = self.get_object()
        project = serializer.validated_data.get("project", instance.project)
        workspace = project.workspace if project else instance.workspace
        serializer.save(workspace=workspace)
