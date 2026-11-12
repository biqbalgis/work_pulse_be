from rest_framework import viewsets, permissions, serializers
from .models import Tag
from .serializers import TagSerializer
from user_permissions.drf import ActionPermissions, require
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity

class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    # Tags label projects' time, so managing them goes with editing projects.
    permission_classes = [permissions.IsAuthenticated, ActionPermissions]
    action_permissions = {
        "update": "projects.edit", "partial_update": "projects.edit", "destroy": "projects.edit",
    }

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        return Tag.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        workspace = get_user_primary_workspace(self.request.user)
        require(self.request.user, workspace.id if workspace else None, "projects.edit",
                "You don't have permission to add tags.")
        instance = serializer.save(workspace=workspace)
        log_activity(self.request.user, "CREATE", "Tag", instance.id,request=self.request)
