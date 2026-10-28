"""
Archived projects (Project.archived_at): closed for good.

  * Hidden from every project list and dropdown (archiving also sets is_active=False, which the lists already
    filter on); the Projects page asks for them with ?archived=1.
  * No time can be logged on them, moved onto them, or changed on them: ensure_open() is called wherever time is
    saved (timesheet, bulk edit, field tickets and their revisions).
  * Reports keep their time up to the moment they were archived and nothing after: report queries go through
    before_archive() / tickets_before_archive().
"""

from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def archived_message(project):
    when = timezone.localtime(project.archived_at).strftime("%b %d, %Y") if project.archived_at else ""
    return f"{project.name} is archived{f' (since {when})' if when else ''}, so no time can be logged or changed on it."


def ensure_open(project):
    """Raise a 400 when `project` is archived."""
    if project is not None and project.archived_at is not None:
        raise ValidationError(archived_message(project))


def before_archive(queryset, prefix=""):
    """Time entries (or rows pointing at one through `prefix`, e.g. "time_entry__") minus any on an archived
    project after it was archived."""
    return queryset.exclude(
        Q(**{f"{prefix}project__archived_at__isnull": False})
        & Q(**{f"{prefix}start_time__gt": F(f"{prefix}project__archived_at")})
    )


def tickets_before_archive(queryset):
    """Field tickets minus any on an archived project dated after the day it was archived."""
    return queryset.exclude(
        Q(project__archived_at__isnull=False) & Q(lem_date__gt=F("project__archived_at__date"))
    )
