from contextvars import ContextVar

from workspaces.models import WorkspaceMember

# ── Per-request membership cache ─────────────────────────────────────────────────────────────────────────────────
# Almost every endpoint asks "which workspaces / what role does this user have?" several times (permission class,
# get_queryset, serializer, ...). Each ask used to be its own query. Inside a request (see
# core.middleware.membership_cache_middleware) the first ask loads the user's memberships and the rest reuse them.
# Outside a request (management commands, scripts, tests calling helpers directly) nothing is cached, so behaviour
# there is unchanged.
_request_cache = ContextVar("workspace_membership_cache", default=None)


def begin_request_cache():
    return _request_cache.set({})


def end_request_cache(token):
    _request_cache.reset(token)


def clear_membership_cache(*_args, **_kwargs):
    """Call after changing memberships mid-request (also wired to WorkspaceMember save/delete signals)."""
    cache = _request_cache.get()
    if cache is not None:
        cache.clear()


def user_memberships(user):
    """The user's (non-deleted) WorkspaceMember rows, ordered by pk — same order `.first()` used to pick from."""
    cache = _request_cache.get()
    if cache is not None and user.pk in cache:
        return cache[user.pk]
    rows = list(WorkspaceMember.objects.filter(user=user).select_related("workspace").order_by("pk"))
    if cache is not None:
        cache[user.pk] = rows
    return rows


def prefetch_memberships(user_ids):
    """Load the memberships of many users in ONE query and seed the request cache with them (so a ticket with a
    dozen employees costs one query, not one per employee). No-op outside a request."""
    cache = _request_cache.get()
    if cache is None:
        return
    wanted = {uid for uid in user_ids if uid not in cache}
    if not wanted:
        return
    grouped = {uid: [] for uid in wanted}
    for member in WorkspaceMember.objects.filter(user_id__in=wanted).select_related("workspace").order_by("pk"):
        grouped[member.user_id].append(member)
    cache.update(grouped)


def first_membership(user):
    rows = user_memberships(user)
    return rows[0] if rows else None


def member_workspace_ids(user):
    return [m.workspace_id for m in user_memberships(user)]


def is_member(user, workspace):
    """True if `user` belongs to `workspace` (a Workspace or its id). Superusers are not special-cased here."""
    workspace_id = str(getattr(workspace, "pk", workspace))
    return any(str(m.workspace_id) == workspace_id for m in user_memberships(user))


def get_user_workspace_ids(user):
    if user.is_superuser:
        from workspaces.models import Workspace
        return Workspace.objects.values_list('id', flat=True)
    return member_workspace_ids(user)

def get_user_primary_workspace(user):
    if user.is_superuser:
        return None
    return first_membership(user).workspace


def resolve_target_user(request_user, target_user_id):
    """
    "Act for another employee" support (an admin viewing / adding time on someone's timesheet).

    Returns (user, workspace):
      - no target, or the caller's own id  -> (request_user, None); callers keep their normal behaviour
      - a superuser                        -> any active user, in that user's first workspace
      - a workspace admin                  -> a user who is a member of a workspace the caller ADMINISTERS,
                                              in that workspace
    Anyone else, or a target outside the caller's workspaces, is refused.
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import PermissionDenied, ValidationError
    from users.models import User

    if not target_user_id or str(target_user_id) == str(request_user.id):
        return request_user, None

    try:
        target = User.objects.get(id=target_user_id, is_active=True)
    except (User.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        raise ValidationError("User not found.")

    memberships = WorkspaceMember.objects.filter(user=target, workspace__is_deleted=False)
    if request_user.is_superuser:
        member = memberships.first()
    else:
        administered = WorkspaceMember.objects.filter(
            user=request_user, role="admin", workspace__is_deleted=False,
        ).values_list("workspace_id", flat=True)
        member = memberships.filter(workspace_id__in=administered).first()
    if not member:
        raise PermissionDenied("You can only work on the timesheet of a member of a workspace you administer.")
    return target, member.workspace
