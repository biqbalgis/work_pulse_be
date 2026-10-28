"""Rules every way of saving time follows (timesheet, bulk entry and edit, field tickets and their revisions)."""

from django.utils import timezone
from rest_framework.exceptions import ValidationError


def ensure_not_future(day):
    """Time can be logged for today or any earlier day, never a future one. `day` is the Mountain-time day the work
    starts on (it may run past midnight into tomorrow)."""
    today = timezone.localdate()
    if day > today:
        raise ValidationError(
            f"Time can't be logged for a future date ({day:%b %d, %Y}). The latest date allowed is today, {today:%b %d, %Y}."
        )
