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
            return OrganizationAsset.objects.filter(is_active=True)

        # Get workspace for logged-in user
        workspace_ids = WorkspaceMember.objects.filter(
            user=user
        ).values_list("workspace_id", flat=True)

        return OrganizationAsset.objects.filter(
            workspace_id__in=workspace_ids,
            is_active=True
        )

    def perform_create(self, serializer):
        user = self.request.user
        wm = WorkspaceMember.objects.filter(user=user).first()

        if not wm:
            raise ValidationError("You are not part of any workspace.")

        serializer.save(workspace=wm.workspace)

    def perform_update(self, serializer):
        instance = self.get_object()
        serializer.save(workspace=instance.workspace)
