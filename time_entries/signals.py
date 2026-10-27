"""Keep amounts in line with the pay rules (core.utils.pay_rules): whenever someone's time or the rates on it
change, reprice that person's whole day, since one entry can move where another's overtime starts. A day the
entry moved away from is repriced too."""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.utils.pay_rules import entry_day, reprice_days
from organization_asset.models import AssetUsage
from .models import TimeEntry


@receiver(pre_save, sender=TimeEntry)
def remember_old_day(sender, instance, raw=False, **kwargs):
    if raw or instance._state.adding:
        return
    old = TimeEntry.all_objects.filter(pk=instance.pk).only("user_id", "start_time").first()
    instance._old_user_day = (old.user_id, entry_day(old)) if old and old.start_time else None


@receiver(post_save, sender=TimeEntry)
@receiver(post_delete, sender=TimeEntry)
def reprice_entry_day(sender, instance, raw=False, **kwargs):
    if raw or not instance.start_time:
        return
    days = {(instance.user_id, entry_day(instance))}
    if getattr(instance, "_old_user_day", None):
        days.add(instance._old_user_day)
    reprice_days(days)


@receiver(post_save, sender=AssetUsage)
@receiver(post_delete, sender=AssetUsage)
def reprice_usage_day(sender, instance, raw=False, **kwargs):
    if raw:
        return
    entry = TimeEntry.all_objects.filter(pk=instance.time_entry_id).only("user_id", "start_time").first()
    if entry and entry.start_time:
        reprice_days({(entry.user_id, entry_day(entry))})
