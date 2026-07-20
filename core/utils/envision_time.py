"""
Envision GEO time handling.

The Envision APIs (field-ticket entry, Envision LEM reports, Envision payroll
report) always work in Mountain Time from the frontend's point of view:
  - Field Ticket start time is a fixed 6:00 AM MST/MDT.
  - Timesheet entries default to 9:00 AM MST/MDT but can be changed.
  - Report date ranges (date_from/date_to) are Mountain calendar days.

The DB always stores UTC (Django's USE_TZ=True). These helpers are the single
place that does the MST <-> UTC conversion for those Envision-specific flows —
other (non-Envision) workspaces/endpoints are unaffected.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

ENVISION_TZ = ZoneInfo("America/Edmonton")


def envision_local_to_utc(date_val, time_val):
    """Combine a date + time-of-day, treating it as Mountain Time wall-clock,
    and return the equivalent UTC-aware datetime for storage."""
    naive = dt.datetime.combine(date_val, time_val)
    return dj_timezone.make_aware(naive, ENVISION_TZ).astimezone(dt.timezone.utc)


def envision_day_bounds_utc(date_val):
    """Return (start, end) UTC-aware datetimes spanning one Mountain-Time
    calendar day — i.e. 00:00:00 to 23:59:59.999999 local, in UTC.
    Use this instead of `start_time__date=` filters, which depend on
    Django's global TIME_ZONE (UTC here), not Mountain Time."""
    start_local = dt.datetime.combine(date_val, dt.time.min)
    end_local = dt.datetime.combine(date_val, dt.time.max)
    start_utc = dj_timezone.make_aware(start_local, ENVISION_TZ).astimezone(dt.timezone.utc)
    end_utc = dj_timezone.make_aware(end_local, ENVISION_TZ).astimezone(dt.timezone.utc)
    return start_utc, end_utc


def utc_to_envision_local(utc_dt):
    """Convert a UTC-aware (or naive-assumed-UTC) datetime to Mountain Time
    for display back to the frontend."""
    if utc_dt is None:
        return None
    if dj_timezone.is_aware(utc_dt):
        return utc_dt.astimezone(ENVISION_TZ)
    return dj_timezone.make_aware(utc_dt, dt.timezone.utc).astimezone(ENVISION_TZ)
