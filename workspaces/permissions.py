from rest_framework import permissions
from rest_framework.permissions import SAFE_METHODS, BasePermission

from core.utils.workspace_utils import user_memberships
from workspaces.models import WorkspaceMember


def _role_in_view_workspace(user, view, roles):
    """
    True when the user holds one of `roles` in the workspace the view targets (`view.workspace`), or — when the
    view names none — in the user's first workspace. Same rule the three Is* classes below always used, answered
    from the per-request membership cache instead of two queries each.
    """
    memberships = user_memberships(user)
    view_workspace = getattr(view, "workspace", None)
    if view_workspace is not None:
        workspace_id = getattr(view_workspace, "pk", view_workspace)
    elif memberships:
        workspace_id = memberships[0].workspace_id
    else:
        return False
    workspace_id = str(workspace_id)
    return any(str(m.workspace_id) == workspace_id and m.role in roles for m in memberships)


def get_user_role_in_workspace(user, workspace=None):
    """Helper function to get a user's role within a workspace."""
    if not user or not user.is_authenticated:
        return None

    if user.is_superuser:
        return 'superuser'

    members = user_memberships(user)
    if workspace:
        workspace_id = getattr(workspace, "pk", workspace)
        members = [m for m in members if str(m.workspace_id) == str(workspace_id)]
    return members[0].role if members else None


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

        return _role_in_view_workspace(user, view, ('admin',))


class IsWorkspaceManager(permissions.BasePermission):
    """Allows access to managers or admins within the workspace."""
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True

        return _role_in_view_workspace(user, view, ('manager', 'admin'))


class IsWorkspaceUser(permissions.BasePermission):
    """Allows access to all workspace members (admin, manager, user)."""
    def has_permission(self, request, view):
        user = request.user
        if user.is_superuser:
            return True

        return _role_in_view_workspace(user, view, ('user', 'manager', 'admin', 'field_manager'))

class IsWorkspaceAdminOrSuperUser(BasePermission):
    """
    Read allowed for all workspace members.
    Write allowed only for workspace admin or superuser.
    """

    def has_permission(self, request, view):
        user = request.user

        # Must be authenticated
        if not user or not user.is_authenticated:
            return False

        # Superuser always allowed
        if user.is_superuser:
            return True

        # Check workspace membership
        memberships = user_memberships(user)
        if not memberships:
            return False

        # Allow READ for any workspace member
        if request.method in SAFE_METHODS:
            return True

        # Allow WRITE only for workspace admin
        return any(m.role == "admin" for m in memberships)

# ── Per-workspace role checks for WRITES ─────────────────────────────────────
# The classes above decide access from one arbitrary membership. These helpers answer the precise question
# "does this user hold one of these roles IN THIS workspace?" (one small indexed lookup, only on writes).

MANAGE_ROLES = ("admin", "manager")
ADMIN_ONLY = ("admin",)


def get_member_role(user, workspace):
    """The user's active role in `workspace` (a Workspace or its id), or None."""
    workspace_id = getattr(workspace, "pk", workspace)
    return next((m.role for m in user_memberships(user) if str(m.workspace_id) == str(workspace_id) and m.is_active), None)


def has_workspace_role(user, workspace, roles=MANAGE_ROLES):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if workspace is None:
        return False
    return get_member_role(user, workspace) in roles


def require_workspace_role(user, workspace, roles=MANAGE_ROLES, message="You do not have permission to do this in this workspace."):
    """Raise 403 unless the user holds one of `roles` in `workspace` (superusers always pass)."""
    from rest_framework.exceptions import PermissionDenied

    if not has_workspace_role(user, workspace, roles):
        raise PermissionDenied(message)
