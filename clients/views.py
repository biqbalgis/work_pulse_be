from rest_framework import viewsets, permissions, serializers, filters
from .models import Client
from .serializers import ClientSerializer
from core.utils.logger import log_activity
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceManager, IsSuperUser

class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'workspace__name', 'contact_info']
    ordering_fields = ['name', 'workspace__name', 'contact_info', 'created_at']

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        queryset = Client.objects.filter(is_deleted=False)

        if user.is_superuser:
            workspace_id = self.request.query_params.get('workspace')
            if not workspace_id:
                raise serializers.ValidationError({"workspace": "Workspace parameter is required for superusers."})
            queryset = queryset.filter(workspace_id=workspace_id)
        else:
            workspace_ids = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)
            queryset = queryset.filter(workspace_id__in=workspace_ids)
            
            # Optional: allow further filtering
            workspace_id = self.request.query_params.get('workspace')
            if workspace_id:
                queryset = queryset.filter(workspace_id=workspace_id)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        member = WorkspaceMember.objects.filter(user=user).first()
        if not member:
            raise serializers.ValidationError("User is not part of any workspace.")
        instance = serializer.save(workspace=member.workspace)
        log_activity(user, "CREATE", "Client", instance.id, request=self.request)

    def perform_destroy(self, instance):
        log_activity(self.request.user, "DELETE", "Client", instance.id,request=self.request)
        instance.delete()
