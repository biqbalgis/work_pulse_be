from django.db.models import Case, DecimalField, F, Value, When
from rest_framework import viewsets, permissions, filters
from rest_framework.exceptions import ValidationError
from .models import OrganizationAsset
from .serializers import OrganizationAssetSerializer
from workspaces.models import WorkspaceMember
from workspaces.permissions import require_workspace_role

# Asset rates feed cost calculations, so changing assets is admin/manager-only (reading is unchanged).
ASSET_WRITE_DENIED = "Only a workspace admin or manager can change assets."


class OrganizationAssetViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationAssetSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'workspace__name', 'project__name', 'charge_type']
    ordering_fields = ['name', 'workspace__name', 'project__name', 'charge_type', 'total_quantity', 'effective_rate']

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

        # "Rate" has no single backend column — hourly vs quantity charge types
        # store their rate in different fields — so annotate the effective one
        # to make the column sortable.
        qs = qs.annotate(
            effective_rate=Case(
                When(charge_type='hourly', then=F('hourly_rate')),
                When(charge_type='quantity', then=F('quantity_rate')),
                default=Value(0),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )

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

        require_workspace_role(user, workspace, message=ASSET_WRITE_DENIED)
        serializer.save(workspace=workspace)

    def perform_update(self, serializer):
        instance = self.get_object()
        project = serializer.validated_data.get("project", instance.project)
        workspace = project.workspace if project else instance.workspace
        require_workspace_role(self.request.user, instance.workspace, message=ASSET_WRITE_DENIED)
        if workspace.pk != instance.workspace_id:
            require_workspace_role(self.request.user, workspace, message=ASSET_WRITE_DENIED)
        serializer.save(workspace=workspace)

    def perform_destroy(self, instance):
        require_workspace_role(self.request.user, instance.workspace, message=ASSET_WRITE_DENIED)
        instance.delete()
