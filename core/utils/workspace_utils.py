from workspaces.models import WorkspaceMember

def get_user_workspace_ids(user):
    if user.is_superuser:
        from workspaces.models import Workspace
        return Workspace.objects.values_list('id', flat=True)
    return WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)

def get_user_primary_workspace(user):
    return WorkspaceMember.objects.filter(user=user).first().workspace if not user.is_superuser else None


def resolve_target_user(request_user, target_user_id, code="timesheet.add_all",
                        message="You don't have permission to work on other people's time."):
    """
    "Act for another employee" support (viewing / adding time on someone else's timesheet).

    Returns (user, workspace):
      - no target, or the caller's own id  -> (request_user, None); callers keep their normal behaviour
      - a superuser                        -> any active user, in that user's first workspace
      - anyone with permission `code`      -> a member of a workspace where the caller has it, in that workspace
    Anyone else, or a target outside those workspaces, is refused.
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
        from user_permissions.access import workspaces_with

        allowed = workspaces_with(request_user, code) or set()
        member = memberships.filter(workspace_id__in=allowed).first()
    if not member:
        raise PermissionDenied(message)
    return target, member.workspace
