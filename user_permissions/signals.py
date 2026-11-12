from django.db.models.signals import post_save
from django.dispatch import receiver

from workspaces.models import WorkspaceMember

from .access import ensure_access, forget_cached


@receiver(post_save, sender=WorkspaceMember)
def give_new_members_access(sender, instance, created, raw=False, **kwargs):
    """Every member has an access record and a permission group (the one matching their role, else the default)."""
    if raw:
        return
    if created:
        ensure_access(instance)
    forget_cached()
