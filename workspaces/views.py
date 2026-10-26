from rest_framework import viewsets, permissions, filters
from rest_framework.exceptions import PermissionDenied
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from .models import Workspace, WorkspaceMember
from .serializers import WorkspaceSerializer, WorkspaceMemberSerializer
from core.utils.logger import log_activity
from .permissions import ADMIN_ONLY, MANAGE_ROLES, get_member_role, has_workspace_role, require_workspace_role


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
        # New workspaces are made by superusers or by existing workspace admins; a plain member (or anyone else)
        # cannot spawn a workspace and thereby become an admin.
        if not user.is_superuser and not WorkspaceMember.objects.filter(
            user=user, role="admin", is_active=True
        ).exists():
            raise PermissionDenied("Only an admin can create a workspace.")
        workspace = serializer.save(created_by=user)

        # Automatically make the creator an admin
        WorkspaceMember.objects.create(
            workspace=workspace,
            user=user,
            role='admin'
        )
        log_activity(user, "CREATE", "Workspace", workspace.id, request=self.request)
        return workspace

    def perform_update(self, serializer):
        require_workspace_role(self.request.user, serializer.instance, ADMIN_ONLY, "Only a workspace admin can change it.")
        serializer.save()

    def perform_destroy(self, instance):
        require_workspace_role(self.request.user, instance, ADMIN_ONLY, "Only a workspace admin can delete it.")
        instance.delete()


class WorkspaceMemberViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceMemberSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'role', 'workspace__name']
    ordering_fields = ['user__first_name', 'user__email', 'role', 'group', 'workspace__name', 'joined_at']

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

        group = self.request.query_params.get("group")
        if group == "none":
            qs = qs.filter(group__isnull=True)
        elif group:
            qs = qs.filter(group=group)

        return qs.select_related("user", "workspace")

    # Writes: admins (and managers, for non-admin members) of the member's workspace — never a plain member.
    # Reads above are unchanged.
    def perform_create(self, serializer):
        workspace = serializer.validated_data["workspace"]
        require_workspace_role(self.request.user, workspace, ADMIN_ONLY, "Only a workspace admin can add members.")
        serializer.save()

    def perform_update(self, serializer):
        member = serializer.instance
        actor = self.request.user
        data = serializer.validated_data

        # A membership is never moved to another workspace or user.
        if ("workspace" in data and data["workspace"].pk != member.workspace_id) or (
            "user" in data and data["user"].pk != member.user_id
        ):
            raise PermissionDenied("A membership cannot be moved to another workspace or user.")

        require_workspace_role(actor, member.workspace, MANAGE_ROLES, "Only a workspace admin or manager can edit members.")

        if not actor.is_superuser:
            caller_role = get_member_role(actor, member.workspace)
            new_role = data.get("role", member.role)
            if new_role != member.role:
                if member.user_id == actor.id:
                    raise PermissionDenied("You cannot change your own role.")
                if caller_role != "admin" and (new_role == "admin" or member.role == "admin"):
                    raise PermissionDenied("Only an admin can grant or change the admin role.")
        serializer.save()

    def perform_destroy(self, instance):
        actor = self.request.user
        require_workspace_role(actor, instance.workspace, MANAGE_ROLES, "Only a workspace admin or manager can remove members.")
        if instance.role == "admin" and not has_workspace_role(actor, instance.workspace, ADMIN_ONLY):
            raise PermissionDenied("Only an admin can remove an admin.")
        instance.delete()

