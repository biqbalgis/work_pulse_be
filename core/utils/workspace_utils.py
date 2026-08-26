from workspaces.models import WorkspaceMember

def get_user_workspace_ids(user):
    if user.is_superuser:
        from workspaces.models import Workspace
        return Workspace.objects.values_list('id', flat=True)
    return WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)

def get_user_primary_workspace(user):
    return WorkspaceMember.objects.filter(user=user).first().workspace if not user.is_superuser else None
