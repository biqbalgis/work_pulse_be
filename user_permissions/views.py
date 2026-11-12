"""
Permissions API (see user_permissions.catalog for the list of permissions, user_permissions.access for the rules)
===============================================================================================================

GET    /api/user-permissions/catalog/                         every permission, by area
GET    /api/user-permissions/me/?workspace=<id>               my permissions (+ the old menu flags)
GET    /api/user-permissions/?workspace=<id>                  members with their group and personal extras
GET    /api/user-permissions/<user_id>/?workspace=<id>        one member's access
PATCH  /api/user-permissions/<user_id>/?workspace=<id>        {group, granted, revoked}
GET    /api/permission-groups/?workspace=<id>                 the workspace's groups
POST   /api/permission-groups/                                {workspace, name, description, permissions}
PATCH  /api/permission-groups/<id>/                           change a group (not the Administrator group)
DELETE /api/permission-groups/<id>/                           only a group nobody is in

Changing groups or anyone's access needs "team.manage_permissions" in that workspace. Listing the groups (to pick
one for a new user) needs any of team.view / team.add / team.edit too.
"""

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspaces.models import Workspace, WorkspaceMember

from .access import (
    AccessError, ensure_access, ensure_starter_groups, forget_cached, has_perm, legacy_modules, permissions_for,
    set_access,
)
from .catalog import ALL_CODES, catalog_json
from .models import PermissionGroup, UserPermission

MANAGE = "team.manage_permissions"
SEE_GROUPS = (MANAGE, "team.view", "team.add", "team.edit")


def _workspace_from(request, required=True):
    """The workspace asked for (?workspace= / body), else the caller's first active one. Returns (workspace, error)."""
    body = request.data if hasattr(request.data, "get") else {}
    workspace_id = request.query_params.get("workspace") or body.get("workspace")
    if workspace_id:
        workspace = Workspace.objects.filter(id=workspace_id).first()
        if workspace is None:
            return None, Response({"error": "Workspace not found."}, status=404)
        if not request.user.is_superuser and not WorkspaceMember.objects.filter(
                user=request.user, workspace=workspace, is_active=True).exists():
            return None, Response({"error": "Workspace not found."}, status=404)
        return workspace, None
    member = WorkspaceMember.objects.filter(user=request.user, is_active=True).select_related("workspace").first()
    if member:
        return member.workspace, None
    if request.user.is_superuser and not required:
        return Workspace.objects.first(), None
    if request.user.is_superuser:
        return None, Response({"error": "Choose a workspace."}, status=400)
    return None, Response({"error": "You do not belong to any workspace."}, status=404)


def _forbidden(message="You don't have permission to manage permissions in this workspace."):
    return Response({"error": message}, status=403)


def _group_json(group, member_counts=None):
    return {
        "id": str(group.id),
        "workspace": str(group.workspace_id),
        "name": group.name,
        "description": group.description,
        "permissions": sorted(ALL_CODES) if group.is_system else sorted(set(group.permissions) & ALL_CODES),
        "is_system": group.is_system,
        "is_default": group.is_default,
        "member_count": (member_counts or {}).get(group.id, 0),
    }


def _member_counts(workspace):
    counts = {}
    rows = UserPermission.objects.filter(
        workspace=workspace, group__isnull=False,
        user__memberships__workspace=workspace, user__memberships__is_deleted=False,
    ).values_list("group_id", flat=True)
    for group_id in rows:
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def _access_json(member, access):
    codes = permissions_for(member.user, member.workspace_id) if member.is_active else frozenset()
    user = member.user
    return {
        "user_id": str(user.id),
        "user_full_name": user.get_full_name(),
        "user_email": user.email,
        "workspace_id": str(member.workspace_id),
        "is_active": member.is_active and user.is_active,
        "group": str(access.group_id) if access.group_id else None,
        "group_name": access.group.name if access.group_id else "",
        "is_administrator": bool(access.group_id and access.group.is_system),
        "granted": sorted(access.granted or []),
        "revoked": sorted(access.revoked or []),
        "permissions": sorted(codes),
        **legacy_modules(codes),
    }


class CatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"areas": catalog_json()})


class MyPermissionsView(APIView):
    """GET /api/user-permissions/me/ — my permissions in a workspace, plus the old menu flags worked out from them."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _workspace_from(request, required=False)
        if error:
            return error
        if workspace is None:
            return Response({"error": "You do not belong to any workspace."}, status=404)
        member = WorkspaceMember.objects.filter(user=request.user, workspace=workspace).first()
        if member:
            ensure_access(member)
        codes = permissions_for(request.user, workspace.id)
        access = UserPermission.objects.filter(user=request.user, workspace=workspace).select_related("group").first()
        return Response({
            "user_id": str(request.user.id),
            "workspace_id": str(workspace.id),
            "workspace_name": workspace.name,
            "group": str(access.group_id) if access and access.group_id else None,
            "group_name": access.group.name if access and access.group_id else ("Superuser" if request.user.is_superuser else ""),
            "is_administrator": request.user.is_superuser or bool(access and access.group_id and access.group.is_system),
            "permissions": sorted(codes),
            **legacy_modules(codes),
        })


class MemberAccessListView(APIView):
    """GET /api/user-permissions/?workspace= — every member with their group (for the Permissions page)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _workspace_from(request)
        if error:
            return error
        if not has_perm(request.user, workspace.id, MANAGE):
            return _forbidden()
        members = list(WorkspaceMember.objects.filter(workspace=workspace).select_related("user")
                       .order_by("user__first_name", "user__last_name", "user__email"))
        for member in members:
            ensure_access(member)
        accesses = {a.user_id: a for a in UserPermission.objects.filter(workspace=workspace).select_related("group")}
        return Response({"results": [_access_json(m, accesses[m.user_id]) for m in members]})


class UserPermissionDetailView(APIView):
    """GET / PATCH /api/user-permissions/<user_id>/?workspace= — one member's group and personal extras."""

    permission_classes = [IsAuthenticated]

    def _member(self, request, user_id):
        workspace, error = _workspace_from(request)
        if error:
            return None, error
        if not has_perm(request.user, workspace.id, MANAGE):
            return None, _forbidden()
        member = WorkspaceMember.objects.filter(user_id=user_id, workspace=workspace).select_related("user").first()
        if member is None:
            return None, Response({"error": "User not found in this workspace."}, status=404)
        return member, None

    def get(self, request, user_id):
        member, error = self._member(request, user_id)
        if error:
            return error
        return Response(_access_json(member, ensure_access(member)))

    def patch(self, request, user_id):
        member, error = self._member(request, user_id)
        if error:
            return error
        group = None
        if request.data.get("group"):
            group = PermissionGroup.objects.filter(id=request.data["group"], workspace_id=member.workspace_id).first()
            if group is None:
                return Response({"error": "Choose one of this workspace's permission groups."}, status=400)
        lists = {}
        for key in ("granted", "revoked"):
            if key in request.data:
                value = request.data.get(key) or []
                if not isinstance(value, list) or not set(value) <= ALL_CODES:
                    return Response({"error": f"Unknown permission in {key}."}, status=400)
                lists[key] = value
        try:
            access = set_access(member, group=group, changed_by=request.user, **lists)
        except AccessError as exc:
            return Response({"error": str(exc)}, status=400)
        access.refresh_from_db()
        return Response(_access_json(member, access))


class PermissionGroupListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _workspace_from(request)
        if error:
            return error
        if not any(has_perm(request.user, workspace.id, code) for code in SEE_GROUPS):
            return _forbidden("You don't have permission to see this workspace's groups.")
        ensure_starter_groups(workspace)
        counts = _member_counts(workspace)
        groups = PermissionGroup.objects.filter(workspace=workspace)
        return Response({
            "results": [_group_json(g, counts) for g in groups],
            "can_manage": has_perm(request.user, workspace.id, MANAGE),
        })

    def post(self, request):
        workspace, error = _workspace_from(request)
        if error:
            return error
        if not has_perm(request.user, workspace.id, MANAGE):
            return _forbidden()
        data, problem = _clean_group(request.data)
        if problem:
            return Response({"error": problem}, status=400)
        try:
            with transaction.atomic():
                group = PermissionGroup.objects.create(workspace=workspace, created_by=request.user, **data)
                if group.is_default:
                    PermissionGroup.objects.filter(workspace=workspace).exclude(id=group.id).update(is_default=False)
        except IntegrityError:
            return Response({"error": f'There is already a group called "{data["name"]}".'}, status=400)
        forget_cached()
        return Response(_group_json(group), status=201)


class PermissionGroupDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _group(self, request, pk):
        group = get_object_or_404(PermissionGroup, pk=pk)
        if not has_perm(request.user, group.workspace_id, MANAGE):
            return None, _forbidden()
        return group, None

    def get(self, request, pk):
        group, error = self._group(request, pk)
        if error:
            return error
        return Response(_group_json(group, _member_counts(group.workspace)))

    def patch(self, request, pk):
        group, error = self._group(request, pk)
        if error:
            return error
        if group.is_system:
            return Response({"error": "The Administrator group always has full access and can't be changed."}, status=400)
        data, problem = _clean_group({**_group_json(group), **request.data})
        if problem:
            return Response({"error": problem}, status=400)
        try:
            with transaction.atomic():
                for key, value in data.items():
                    setattr(group, key, value)
                group.save()
                if group.is_default:
                    PermissionGroup.objects.filter(workspace_id=group.workspace_id).exclude(id=group.id).update(is_default=False)
        except IntegrityError:
            return Response({"error": f'There is already a group called "{data["name"]}".'}, status=400)
        forget_cached()
        return Response(_group_json(group, _member_counts(group.workspace)))

    def delete(self, request, pk):
        group, error = self._group(request, pk)
        if error:
            return error
        if group.is_system:
            return Response({"error": "The Administrator group can't be removed."}, status=400)
        count = _member_counts(group.workspace).get(group.id, 0)
        if count:
            return Response({"error": f'Move the {count} member{"s" if count != 1 else ""} of "{group.name}" to another '
                                      f'group first.'}, status=400)
        if group.is_default:
            return Response({"error": "This is the group new members join. Make another group the default first."},
                            status=400)
        UserPermission.objects.filter(group=group).update(group=None)  # removed members' leftover records
        group.delete()
        forget_cached()
        return Response(status=204)


def _clean_group(data):
    """({name, description, permissions, is_default}, problem)."""
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "Give the group a name."
    if len(name) > 80:
        return None, "Keep the group name under 80 characters."
    permissions = data.get("permissions") or []
    if not isinstance(permissions, list) or not set(permissions) <= ALL_CODES:
        return None, "Unknown permission in the list."
    return {
        "name": name,
        "description": str(data.get("description") or "").strip()[:255],
        "permissions": sorted(set(permissions)),
        "is_default": bool(data.get("is_default")),
    }, None
