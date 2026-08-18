from rest_framework import viewsets, permissions, filters
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity


class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'address', 'created_by__email']
    ordering_fields = ['name', 'created_at', 'created_by__email', 'member_count']

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        # Annotated as an independent correlated subquery (not a plain
        # Count('members') annotate) so it isn't silently corrupted by the
        # members__user filter below reusing the same join — that reuse would
        # make member_count only count the current user's own membership.
        member_count_subquery = (
            WorkspaceMember.objects.filter(workspace=OuterRef('pk'))
            .order_by().values('workspace').annotate(cnt=Count('id')).values('cnt')
        )
        base_queryset = Workspace.objects.all().prefetch_related(
            Prefetch('members', queryset=WorkspaceMember.objects.select_related('user'))
        ).annotate(
            member_count=Coalesce(Subquery(member_count_subquery, output_field=IntegerField()), 0)
        ).order_by('-created_at')

        user = self.request.user
        if user.is_superuser:
            qs = base_queryset
        else:
            qs = base_queryset.filter(members__user=user)

        # Optional filters
        name = self.request.query_params.get('name')
        if name:
            qs = qs.filter(name__icontains=name)

        address = self.request.query_params.get('address')
        if address:
            qs = qs.filter(address__icontains=address)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        workspace = serializer.save(created_by=user)

        # Automatically make the creator an admin
        WorkspaceMember.objects.create(
            workspace=workspace,
            user=user,
            role='admin'
        )
        log_activity(user, "CREATE", "Workspace", workspace.id, request=self.request)
        return workspace


class WorkspaceMemberViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'role', 'workspace__name']
    ordering_fields = ['user__first_name', 'user__email', 'role', 'workspace__name', 'joined_at']

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)
    def get_queryset(self):
        workspace_id = self.request.query_params.get("workspace", None)

        # Superuser sees all
        if self.request.user.is_superuser:
            qs = WorkspaceMember.objects.filter(is_deleted=False, is_active=True)

        else:
            # Normal users see only their workspace
            qs = WorkspaceMember.objects.filter(
                workspace__members__user=self.request.user,
                is_deleted=False,
                is_active=True
            )

        # Filter for specific workspace if provided
        if workspace_id:
            qs = qs.filter(workspace_id=workspace_id)

        return qs.select_related("user", "workspace")
