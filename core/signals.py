from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from core.models import ActivityLog
from core.utils.logger import log_activity

@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    if sender.__name__ == "ActivityLog":
        return
    user = getattr(instance, 'modified_by', None) or getattr(instance, 'created_by', None)
    if user:
        action = 'CREATE' if created else 'UPDATE'
        log_activity(user, action, sender.__name__, instance.id)

@receiver(post_delete)
def log_model_delete(sender, instance, **kwargs):
    if sender.__name__ == "ActivityLog":
        return
    user = getattr(instance, 'modified_by', None)
    if user:
        log_activity(user, 'DELETE', sender.__name__, instance.id)
