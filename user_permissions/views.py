"""
User Permission API
===================
Endpoints
---------
GET  /api/user-permissions/{user_id}/          — retrieve (or auto-create) permissions for a user
PATCH /api/user-permissions/{user_id}/         — update permissions (admin / superuser only)
GET  /api/user-permissions/me/                 — retrieve the requesting user's own permissions

Access rules
------------
- Superuser or workspace admin: full read + write for any user in their workspace.
- Any authenticated user: may read their OWN permissions (GET /me/).
- No other access.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from users.models import User
from workspaces.models import WorkspaceMember
from .models import UserPermission
from .serializers import UserPermissionSerializer


def _is_admin_or_superuser(request_user, workspace=None):
    if request_user.is_superuser:
        return True
    qs = WorkspaceMember.objects.filter(user=request_user, role="admin")
    if workspace:
        qs = qs.filter(workspace=workspace)
    return qs.exists()


def _get_workspace_for_user(request_user, target_user):
    """
    Return the workspace that the requesting user administers and that the
    target user belongs to.  For superusers we use the target user's workspace.
    """
    if request_user.is_superuser:
        member = WorkspaceMember.objects.filter(user=target_user).first()
    else:
        admin_ws_ids = WorkspaceMember.objects.filter(
            user=request_user, role="admin"
        ).values_list("workspace_id", flat=True)
        member = WorkspaceMember.objects.filter(
            user=target_user, workspace_id__in=admin_ws_ids
        ).first()
    return member.workspace if member else None


def _get_or_create_permissions(user, workspace):
    """Return existing UserPermission or create one with all flags False."""
    perm, _ = UserPermission.objects.get_or_create(
        user=user, workspace=workspace
    )
    return perm


class UserPermissionDetailView(APIView):
    """
    GET  /api/user-permissions/{user_id}/   — retrieve permissions
    PATCH /api/user-permissions/{user_id}/  — update permissions (admin / superuser only)
    """

    permission_classes = [IsAuthenticated]

    def _get_target(self, request, user_id):
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None, None, Response({"error": "User not found."}, status=404)

        workspace = _get_workspace_for_user(request.user, target_user)
        if not workspace:
            return None, None, Response(
                {"error": "User not found in your workspace."},
                status=404,
            )
        return target_user, workspace, None

    def get(self, request, user_id):
        # Superuser / admin can read anyone; regular user cannot use this endpoint.
        if not _is_admin_or_superuser(request.user):
            return Response({"error": "Permission denied."}, status=403)

        target_user, workspace, err = self._get_target(request, user_id)
        if err:
            return err

        perm = _get_or_create_permissions(target_user, workspace)
        return Response(UserPermissionSerializer(perm).data)

    def patch(self, request, user_id):
        if not _is_admin_or_superuser(request.user):
            return Response({"error": "Permission denied."}, status=403)

        target_user, workspace, err = self._get_target(request, user_id)
        if err:
            return err

        perm = _get_or_create_permissions(target_user, workspace)
        serializer = UserPermissionSerializer(perm, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        serializer.save()
        return Response(serializer.data)


class MyPermissionsView(APIView):
    """
    GET /api/user-permissions/me/
    Any authenticated user can retrieve their own permissions.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        member = WorkspaceMember.objects.filter(user=request.user).first()
        if not member:
            return Response({"error": "You do not belong to any workspace."}, status=404)

        perm = _get_or_create_permissions(request.user, member.workspace)
        return Response(UserPermissionSerializer(perm).data)
