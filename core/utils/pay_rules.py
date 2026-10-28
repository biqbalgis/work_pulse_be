"""
The pay rules every hour in the app is split and priced by (one place, used by approvals, payroll, reports, field
tickets and the timesheet):

  * Regular time: the first 8 hours a person works in a day (all their jobs together, in start-time order).
  * Overtime, paid 1.5x: hours past 8 in a day.
  * Stat holiday: the first 8 hours are paid 1.5x (counted as overtime), hours past 8 are double time, paid 2x.

Only the day counts (no weekly limit). A "day" is the Mountain-time calendar day the entry starts on. Stat holidays
are Alberta's statutory holidays plus any the workspace adds (workspaces.Holiday).

The premiums apply to what people are paid (labour cost) and to what clients are billed for labour: the entry's
labour amount and hourly crew rates (OPFC, Supervisor Rate...). Equipment and per-unit items stay at their flat rate.

Two views of a day:
  * Pay (payroll, approvals hours, reports): a person's whole day, every job and ticket together.
  * A field ticket (its PDF and what it bills, the stored amounts): only that ticket's own rows, exactly as the
    form showed them, so another ticket or timesheet entry that day never changes it (split_entries(per_ticket=True)).
    Time not on a ticket is priced from the day's other off-ticket time.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

import holidays as holidays_lib
from django.utils.dateparse import parse_datetime

from core.utils.envision_time import utc_to_envision_local

DAILY_RT_HOURS = Decimal("8")
OT_MULTIPLIER = Decimal("1.5")
DT_MULTIPLIER = Decimal("2")
ZERO = Decimal("0")
HUNDREDTH = Decimal("0.01")


@dataclass
class Split:
    """One entry's hours by pay category."""
    rt: Decimal = ZERO
    ot: Decimal = ZERO
    dt: Decimal = ZERO
    holiday: bool = False
    holiday_name: str = ""

    @property
    def total(self):
        return self.rt + self.ot + self.dt

    @property
    def paid_hours(self):
        """Hours weighted by their multiplier: what a rate is multiplied by."""
        return self.rt + self.ot * OT_MULTIPLIER + self.dt * DT_MULTIPLIER

    @property
    def factor(self):
        """paid_hours / hours: 1 for a plain regular day, up to 2 for double time."""
        return self.paid_hours / self.total if self.total else Decimal("1")


@lru_cache(maxsize=16)
def _alberta(year):
    """{date: name} of Alberta's statutory holidays (the same list core.utils.envision_time uses)."""
    return dict(holidays_lib.country_holidays("CA", subdiv="AB", years=[year]).items())


def stat_holidays(years, workspace_id=None):
    """{date: name} of stat holidays in `years`: Alberta's plus the workspace's own."""
    from workspaces.models import Holiday

    years = sorted(set(years))
    found = {}
    for year in years:
        found.update(_alberta(year))
    if workspace_id and years:
        for day, name in Holiday.objects.filter(workspace_id=workspace_id, date__year__in=years).values_list("date", "name"):
            found.setdefault(day, name)
    return found


def _hours(entry):
    return Decimal(entry.duration or 0) / Decimal(60)


def entry_day(entry):
    start = entry.start_time
    if isinstance(start, str):  # set from a string and not reloaded yet
        start = parse_datetime(start)
    return utc_to_envision_local(start).date()


def split_day(entries, holiday_name=None):
    """{entry id: Split} for one person's entries on one day, in the order they were worked. Entries that start at
    the same moment (one day split across jobs) have no real order, so they share the split in proportion to their
    hours instead of the first one taking all the regular time."""
    used = ZERO
    result = {}
    by_start = defaultdict(list)
    for entry in entries:
        by_start[entry.start_time].append(entry)
    for start in sorted(by_start):
        group = by_start[start]
        group_hours = sum((_hours(e) for e in group), ZERO)
        first = min(group_hours, max(DAILY_RT_HOURS - used, ZERO))  # the part within the day's first 8 hours
        used += group_hours
        for entry in group:
            share = _hours(entry) / group_hours if group_hours else ZERO
            within, beyond = first * share, (group_hours - first) * share
            if holiday_name:
                result[entry.id] = Split(rt=ZERO, ot=within, dt=beyond, holiday=True, holiday_name=holiday_name)
            else:
                result[entry.id] = Split(rt=within, ot=beyond)
    return result


def ticket_of(entry_ids):
    """{time entry id: id of the live field ticket it's on} for those entries (entries on none are left out)."""
    from reports.models import LEMReport

    links = LEMReport.time_entries.through.objects.filter(
        timeentry_id__in=list(entry_ids), lemreport__is_deleted=False,
    ).values_list("timeentry_id", "lemreport_id")
    return dict(links)


def split_entries(entries, context=None, per_ticket=False):
    """{entry id: Split} for `entries`. Each person's day is worked out from all their (live) entries that day,
    so an entry on another job earlier in the day moves where overtime starts. Pass `context` (entries already
    loaded that cover those whole days) to skip the extra query, or the entries themselves to split only among
    them. per_ticket=True splits each field ticket's rows on their own (and off-ticket time among itself): what
    a ticket shows and bills."""
    from time_entries.models import TimeEntry

    entries = list(entries)
    if not entries:
        return {}
    days = {(e.user_id, entry_day(e)) for e in entries}
    if context is None:
        users = {user for user, _ in days}
        dates = [day for _, day in days]
        # One query for every person's whole days (a day either side covers the timezone shift).
        from datetime import timedelta
        context = TimeEntry.objects.filter(
            user_id__in=users, is_deleted=False,
            start_time__date__gte=min(dates) - timedelta(days=1), start_time__date__lte=max(dates) + timedelta(days=1),
        ).only("id", "user_id", "workspace_id", "start_time", "duration")
    pool = {e.id: e for e in list(context) + entries if (e.user_id, entry_day(e)) in days}
    tickets = ticket_of(pool) if per_ticket else {}
    grouped = defaultdict(list)
    for entry in pool.values():
        grouped[(entry.user_id, entry_day(entry), tickets.get(entry.id))].append(entry)

    workspace_by_user = {e.user_id: e.workspace_id for e in entries}
    holidays = {}
    for user_id in {u for u, _ in days}:
        years = {d.year for u, d in days if u == user_id}
        holidays[user_id] = stat_holidays(years, workspace_by_user.get(user_id))

    result = {}
    for (user_id, day, _ticket), day_entries in grouped.items():
        result.update(split_day(day_entries, holidays[user_id].get(day)))
    return {e.id: result[e.id] for e in entries}


# ---------------------------------------------------------------------------- pricing
def _is_premium_rate(asset):
    """Hourly crew rates (not equipment) are billed with the overtime / double-time premium."""
    return asset.charge_type == "hourly" and asset.is_equipment is False


def labour_amount(hourly_rate, split):
    if hourly_rate is None:
        return ZERO
    return (Decimal(hourly_rate) * split.paid_hours).quantize(HUNDREDTH)


def usage_amount(usage, entry, split):
    """What an asset usage bills: rate x quantity, with the day's premium on hourly crew rates."""
    asset = usage.asset
    if asset.charge_type == "hourly":
        hours = Decimal(usage.quantity_used) if usage.quantity_used is not None else _hours(entry)
        amount = Decimal(asset.hourly_rate or 0) * hours
        if _is_premium_rate(asset):
            amount *= split.factor
    else:
        amount = Decimal(usage.quantity_used or 0) * Decimal(asset.quantity_rate or 0)
    return amount.quantize(HUNDREDTH)


def reprice_days(user_days):
    """Recompute the labour amount and asset usage amounts of every entry on these (user id, date) days, after
    time was added, changed or removed. Each field ticket is priced from its own rows only (see split_entries
    per_ticket). Entries on an invoiced field ticket keep what was invoiced. Uses queryset updates, so it doesn't
    re-trigger itself."""
    from datetime import timedelta

    from organization_asset.models import AssetUsage
    from time_entries.models import TimeEntry

    user_days = {(u, d) for u, d in user_days if u and d}
    if not user_days:
        return
    dates = [d for _, d in user_days]
    candidates = (
        TimeEntry.objects.filter(
            user_id__in={u for u, _ in user_days}, is_deleted=False,
            start_time__date__gte=min(dates) - timedelta(days=1), start_time__date__lte=max(dates) + timedelta(days=1),
        )
        .prefetch_related("asset_usages__asset", "lem_reports")
    )
    entries = [e for e in candidates if (e.user_id, entry_day(e)) in user_days]
    splits = split_entries(entries, context=entries, per_ticket=True)
    for entry in entries:
        if any(lem.status == "invoiced" and not lem.is_deleted for lem in entry.lem_reports.all()):
            continue
        split = splits[entry.id]
        cost = labour_amount(entry.hourly_rate, split) if entry.hourly_rate is not None else entry.cost
        if entry.hourly_rate is not None and cost != entry.cost:
            TimeEntry.objects.filter(pk=entry.pk).update(cost=cost)
        for usage in entry.asset_usages.all():
            if usage.is_deleted:
                continue
            amount = usage_amount(usage, entry, split)
            if amount != usage.cost:
                AssetUsage.objects.filter(pk=usage.pk).update(cost=amount)
