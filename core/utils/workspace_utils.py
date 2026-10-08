from workspaces.models import WorkspaceMember

def get_user_workspace_ids(user):
    if user.is_superuser:
        from workspaces.models import Workspace
        return Workspace.objects.values_list('id', flat=True)
    return WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)

def get_user_primary_workspace(user):
    return WorkspaceMember.objects.filter(user=user).first().workspace if not user.is_superuser else None


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
