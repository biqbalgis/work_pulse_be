from rest_framework import permissions
from workspaces.models import WorkspaceMember

class IsSuperUser(permissions.BasePermission):
    """Allow full access to Django superusers."""
    def has_permission(self, request, view):
        return request.user and request.user.is_superuser


class IsWorkspaceAdmin(permissions.BasePermission):
    """Allows access only to workspace admins."""
    def has_permission(self, request, view):
        return WorkspaceMember.objects.filter(
            user=request.user, role='admin'
        ).exists()


class IsWorkspaceManager(permissions.BasePermission):
    """Allows access to admins only."""
    def has_permission(self, request, view):
        return WorkspaceMember.objects.filter(
            user=request.user, role__in=['admin']
        ).exists()


class IsWorkspaceUser(permissions.BasePermission):
    """Allows access to all workspace members (user, manager, admin)."""
    def has_permission(self, request, view):
        return WorkspaceMember.objects.filter(
            user=request.user
        ).exists()
