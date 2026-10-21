from django.db.models.signals import post_save
from django.dispatch import receiver

from workspaces.models import WorkspaceMember

from .models import DEFAULT_NEW_MEMBER_PERMISSIONS, UserPermission


@receiver(post_save, sender=WorkspaceMember)
def create_default_permissions(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    UserPermission.objects.get_or_create(
        user=instance.user,
        workspace=instance.workspace,
        defaults=DEFAULT_NEW_MEMBER_PERMISSIONS,
    )
