from rest_framework import permissions
from workspaces.models import WorkspaceMember

def get_user_role_in_workspace(user, workspace=None):
    """Helper function to get a user's role within a workspace."""
    if not user or not user.is_authenticated:
        return None

    if user.is_superuser:
        return 'superuser'

    query = WorkspaceMember.objects.filter(user=user)
    if workspace:
        query = query.filter(workspace=workspace)

    member = query.first()
    return member.role if member else None


class IsSuperUser(permissions.BasePermission):
    """Full access for Django superusers."""
    def has_permission(self, request, view):
        return request.user and request.user.is_superuser


class IsWorkspaceAdmin(permissions.BasePermission):
    """Allows access only to admins within their workspace."""
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True

        membership = WorkspaceMember.objects.filter(user=user).first()

        workspace = (
                getattr(view, "workspace", None)
                or (membership.workspace if membership else None))
        if not workspace:
            return False

        return WorkspaceMember.objects.filter(user=user, workspace=workspace, role='admin').exists()


class IsWorkspaceManager(permissions.BasePermission):
    """Allows access to managers or admins within the workspace."""
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True

        membership = WorkspaceMember.objects.filter(user=user).first()

        workspace = (
                getattr(view, "workspace", None)
                or (membership.workspace if membership else None))

        if not workspace:
            return False

        return WorkspaceMember.objects.filter(
            user=user, workspace=workspace, role__in=['manager', 'admin']
        ).exists()


class IsWorkspaceUser(permissions.BasePermission):
    """Allows access to all workspace members (admin, manager, user)."""
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True

        membership = WorkspaceMember.objects.filter(user=user).first()

        workspace = (
                getattr(view, "workspace", None)
                or (membership.workspace if membership else None))
        if not workspace:
            return False

        return WorkspaceMember.objects.filter(
            user=user, workspace=workspace, role__in=['user', 'manager', 'admin']
        ).exists()