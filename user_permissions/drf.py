"""
Checking permissions (user_permissions.catalog) in DRF views.

    class ProjectViewSet(viewsets.ModelViewSet):
        permission_classes = [IsAuthenticated, ActionPermissions]
        action_permissions = {
            "create": "projects.add",
            "update": "projects.edit", "partial_update": "projects.edit",
            "destroy": "projects.delete",
            "archive": "projects.archive",
        }

An action with a code needs it: in the object's workspace for actions on one object (obj.workspace_id, or
view.permission_workspace_of(obj)), else in the workspace the request is for (?workspace= / body "workspace", or
the caller's first active workspace). A tuple means any one of those codes. Actions not listed are left to the
view (lists are already limited to the caller's workspaces).
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from workspaces.models import WorkspaceMember

from .access import has_perm
from .catalog import ALL_CODES

DENIED = "You don't have permission to do that."


def _codes(code):
    codes = (code,) if isinstance(code, str) else tuple(code)
    unknown = set(codes) - ALL_CODES
    assert not unknown, f"Unknown permission {unknown}"
    return codes


def has_any(user, workspace_id, code):
    return any(has_perm(user, workspace_id, c) for c in _codes(code))


def require(user, workspace_id, code, message=DENIED):
    """403 unless `user` has `code` (or one of the codes) in that workspace."""
    if not workspace_id or not has_any(user, workspace_id, code):
        raise PermissionDenied(message)


def request_workspace_id(request):
    """The workspace a request is for: ?workspace= or the body's "workspace" / "workspace_id", else the caller's
    first active workspace."""
    body = request.data if hasattr(request.data, "get") else {}
    workspace_id = request.query_params.get("workspace") or body.get("workspace") or body.get("workspace_id")
    if workspace_id:
        return str(workspace_id)
    member = WorkspaceMember.objects.filter(user=request.user, is_active=True).values_list("workspace_id", flat=True).first()
    return str(member) if member else None


class ActionPermissions(BasePermission):
    message = DENIED

    def _code(self, view):
        return getattr(view, "action_permissions", {}).get(getattr(view, "action", None))

    def has_permission(self, request, view):
        code = self._code(view)
        if code is None or request.user.is_superuser:
            return True
        if getattr(view, "detail", False) or view.kwargs.get(getattr(view, "lookup_url_kwarg", None) or "pk"):
            return True  # decided per object below
        message = getattr(view, "action_permission_messages", {}).get(view.action)
        if message:
            self.message = message
        return has_any(request.user, request_workspace_id(request), code)

    def has_object_permission(self, request, view, obj):
        code = self._code(view)
        if code is None or request.user.is_superuser:
            return True
        message = getattr(view, "action_permission_messages", {}).get(view.action)
        if message:
            self.message = message
        workspace_of = getattr(view, "permission_workspace_of", None)
        workspace_id = workspace_of(obj) if workspace_of else getattr(obj, "workspace_id", None)
        return bool(workspace_id) and has_any(request.user, workspace_id, code)
