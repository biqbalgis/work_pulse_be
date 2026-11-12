"""
Who may do what: a person's effective permissions in a workspace.

    effective = (superuser)             -> every permission
              | (Administrator group)   -> every permission
              | group's permissions + granted to them - revoked from them

Read once per request (core.utils.request_cache), since one API call can ask several questions.
"""

from django.db import transaction

from core.utils import request_cache
from workspaces.models import WorkspaceMember

from .catalog import ADMINISTRATOR, ALL_CODES, DEFAULT_GROUP, LEGACY_MODULE_CODES, STARTER_GROUPS
from .models import PermissionGroup, UserPermission


class AccessError(Exception):
    """A change to someone's access that isn't allowed (shown to the person making it)."""


# ── Reading ────────────────────────────────────────────────────────────────────
def _effective(access):
    if access is None:
        return frozenset()
    group = access.group
    if group is not None and group.is_system:
        return ALL_CODES
    codes = set(group.permissions if group else []) | set(access.granted or [])
    codes -= set(access.revoked or [])
    return frozenset(codes & ALL_CODES)


def permissions_for(user, workspace_id):
    """frozenset of permission codes `user` has in that workspace (empty if they aren't an active member)."""
    if user is None or not user.is_authenticated:
        return frozenset()
    if user.is_superuser:
        return ALL_CODES
    if not workspace_id:
        return frozenset()

    def load():
        if not WorkspaceMember.objects.filter(user=user, workspace_id=workspace_id, is_active=True).exists():
            return frozenset()
        access = UserPermission.objects.filter(user=user, workspace_id=workspace_id).select_related("group").first()
        return _effective(access)

    return request_cache.remember(("permissions", user.pk, str(workspace_id)), load)


def has_perm(user, workspace_id, code):
    return code in permissions_for(user, workspace_id)


def workspaces_with(user, code):
    """Workspace ids where `user` has `code` (None = every workspace, for superusers)."""
    if user.is_superuser:
        return None
    ids = WorkspaceMember.objects.filter(user=user, is_active=True).values_list("workspace_id", flat=True)
    return {ws for ws in ids if has_perm(user, ws, code)}


def legacy_modules(codes):
    """The old on/off section flags (the menu) from a set of permission codes."""
    return {flag: code in codes for flag, code in LEGACY_MODULE_CODES.items()}


def forget_cached():
    """Access changed in this request: re-read it for the rest of the request."""
    request_cache.clear()


# ── Groups of a workspace ──────────────────────────────────────────────────────
def ensure_starter_groups(workspace, created_by=None):
    """The starter groups (Administrator, Manager, Field Manager, Crew) for a workspace that has none yet."""
    groups = {g.name: g for g in PermissionGroup.objects.filter(workspace=workspace)}
    if groups:
        return groups
    for name, role, description, codes in STARTER_GROUPS:
        groups[name] = PermissionGroup.objects.create(
            workspace=workspace, name=name, description=description, permissions=list(codes),
            is_system=name == ADMINISTRATOR, is_default=name == DEFAULT_GROUP, base_role=role, created_by=created_by,
        )
    return groups


def group_for_role(workspace, role):
    """The starter group that matches an old role (for members added the old way), else the default group."""
    groups = ensure_starter_groups(workspace)
    by_role = {g.base_role: g for g in groups.values() if g.name in {n for n, *_ in STARTER_GROUPS}}
    return by_role.get(role) or next((g for g in groups.values() if g.is_default), None)


def ensure_access(member):
    """A member's access record, created (in the group matching their role) if they don't have one."""
    access, created = UserPermission.objects.get_or_create(user_id=member.user_id, workspace_id=member.workspace_id)
    if access.group_id is None:
        access.group = group_for_role(member.workspace, member.role)
        access.save(update_fields=["group", "updated_at"])
    return access


# ── Changing someone's access ──────────────────────────────────────────────────
def _administrators_left(workspace_id, excluding_user_id):
    return UserPermission.objects.filter(
        workspace_id=workspace_id, group__is_system=True,
        user__memberships__workspace_id=workspace_id, user__memberships__is_active=True,
        user__memberships__is_deleted=False, user__is_active=True,
    ).exclude(user_id=excluding_user_id).distinct().count()


@transaction.atomic
def set_access(member, group=None, granted=None, revoked=None, changed_by=None):
    """Put `member` in `group` and/or set their personal extras. Keeps the old role in step with the group (still
    used by checks not moved to permissions yet) and never leaves a workspace without an administrator."""
    access = ensure_access(member)
    if group is not None:
        if group.workspace_id != member.workspace_id:
            raise AccessError("That group belongs to another workspace.")
        if changed_by is not None and changed_by.pk == member.user_id and group.pk != access.group_id \
                and not changed_by.is_superuser:
            raise AccessError("You can't change your own group. Ask another administrator.")
        if access.group and access.group.is_system and not group.is_system \
                and _administrators_left(member.workspace_id, member.user_id) == 0:
            raise AccessError("This is the workspace's last administrator. Make someone else an administrator first.")
        access.group = group
        role = "admin" if group.is_system else (group.base_role or "user")
        if member.role != role:
            member.role = role
            member.save(update_fields=["role"])
    if granted is not None:
        access.granted = sorted(set(granted) & ALL_CODES)
    if revoked is not None:
        access.revoked = sorted(set(revoked) & ALL_CODES)
    # Something both granted and removed: removed wins, so keep the lists apart.
    access.granted = [code for code in access.granted if code not in set(access.revoked)]
    access.save()
    forget_cached()
    return access
