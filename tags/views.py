from rest_framework import viewsets, permissions, serializers
from .models import Tag
from .serializers import TagSerializer
from workspaces.permissions import IsWorkspaceManager, IsSuperUser
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity

class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        workspace_ids = get_user_workspace_ids(user)
        return Tag.objects.filter(workspace_id__in=workspace_ids)

    def perform_create(self, serializer):
        workspace = get_user_primary_workspace(self.request.user)
        instance = serializer.save(workspace=workspace)
        log_activity(self.request.user, "CREATE", "Tag", instance.id,request=self.request)
