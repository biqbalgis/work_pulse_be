from rest_framework import viewsets, permissions, serializers
from .models import Client
from .serializers import ClientSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser

class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Client.objects.filter(is_deleted=False)
        workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
        return Client.objects.filter(workspace_id__in=workspace_ids, is_deleted=False)

    def perform_create(self, serializer):
        user = self.request.user
        member = WorkspaceMember.objects.filter(user=user).first()
        if not member:
            raise serializers.ValidationError("User is not part of any workspace.")
        instance = serializer.save(workspace=member.workspace)
        log_activity(user, "CREATE", "Client", instance.id)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Client", instance.id)
        instance.delete()
