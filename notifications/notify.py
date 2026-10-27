"""Creating notifications for what happens to people's time and field tickets. Called from the views that make the
change (not from model saves), so a notification always means someone did something, and approving a whole week
sends one notification instead of one per entry. Nobody is notified about their own action."""

from datetime import timedelta

from django.utils import timezone

from user_permissions.models import UserPermission
from .models import Notification


def _name(user):
    return (user.get_full_name() or user.email or "Someone").strip() if user else "Someone"


def _day(value):
    return f"{value:%b} {value.day}"


def _hours(minutes):
    return f"{round((minutes or 0) / 60, 2):g} h"


def _week_start(day):
    """The Sunday that starts the timesheet week (weeks run Sunday to Saturday)."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def notify(recipients, actor, kind, title, message="", link="", workspace=None, target_type="", target_id=""):
    """One notification per recipient, skipping the person who did it and duplicates."""
    seen = set()
    rows = []
    for user in recipients:
        if user is None or user.pk in seen or (actor is not None and user.pk == actor.pk):
            continue
        seen.add(user.pk)
        rows.append(Notification(
            recipient=user, actor=actor, workspace=workspace, kind=kind, title=title, message=message,
            link=link, target_type=target_type, target_id=str(target_id or ""),
        ))
    return Notification.objects.bulk_create(rows)


# ---------------------------------------------------------------------------- time
def notify_entry_decision(item, actor, approved, reason=""):
    """The approver approved or sent back one time entry: tell the employee."""
    entry = item.time_entry
    day = timezone.localtime(entry.start_time).date()
    where = entry.project.name if entry.project_id else "no project"
    if approved:
        kind, title = Notification.TIME_APPROVED, "Time approved"
        message = f"{_name(actor)} approved your {_hours(entry.duration)} on {where} ({_day(day)})."
    else:
        kind, title = Notification.TIME_REJECTED, "Time sent back"
        message = f"{_name(actor)} sent back your {_hours(entry.duration)} on {where} ({_day(day)}): {reason}"
    return notify([entry.user], actor, kind, title, message, f"/timesheet?week={_week_start(day).isoformat()}",
                  item.approval.workspace, "time_entry", entry.id)


def notify_week_decision(approval, actor, approved, count, reason=""):
    """The approver approved or sent back a whole week: one notification for it."""
    if not count:
        return []
    span = f"{_day(approval.start_date)} – {_day(approval.end_date)}"
    entries = f"{count} {'entry' if count == 1 else 'entries'}"
    if approved:
        kind, title = Notification.TIME_APPROVED, "Week approved"
        message = f"{_name(actor)} approved your time for {span} ({entries})."
    else:
        kind, title = Notification.TIME_REJECTED, "Week sent back"
        message = f"{_name(actor)} sent back your time for {span} ({entries}): {reason}"
    return notify([approval.user], actor, kind, title, message,
                  f"/timesheet?week={_week_start(approval.start_date).isoformat()}",
                  approval.workspace, "approval", approval.id)


# ---------------------------------------------------------------------------- field tickets
def _ticket_people(lem):
    """Who a ticket concerns: whoever submitted it and everyone whose time is on it."""
    return [lem.requester] + [e.user for e in lem.time_entries.filter(is_deleted=False).select_related("user")]


def _ticket_approvers(lem):
    """Everyone who approves field tickets in the ticket's workspace."""
    if not lem.project_id:
        return []
    return [p.user for p in UserPermission.objects.filter(workspace_id=lem.project.workspace_id, lem_approval=True)
            .select_related("user")]


def notify_ticket(lem, actor, kind, note=""):
    """A field ticket was approved, rejected, invoiced or revised (updated)."""
    number = lem.ticket_number
    about = " · ".join(filter(None, [lem.project.name if lem.project_id else "", _day(lem.lem_date) if lem.lem_date else ""]))
    who = _name(actor)
    recipients = _ticket_people(lem)
    if kind == Notification.TICKET_APPROVED:
        title, message = f"Field ticket {number} approved", f"{who} approved field ticket {number} ({about})."
    elif kind == Notification.TICKET_REJECTED:
        title, message = f"Field ticket {number} rejected", f"{who} rejected field ticket {number} ({about}): {note}"
    elif kind == Notification.TICKET_INVOICED:
        title = f"Field ticket {number} invoiced"
        message = f"{who} put field ticket {number} ({about}) on invoice {lem.invoice_number}. It's locked now."
    else:
        # A revision goes back for approval, so whoever approves tickets hears about it too.
        title = f"Field ticket {number} updated"
        message = f"{who} revised field ticket {number} ({about}). It's waiting for approval again."
        recipients += _ticket_approvers(lem)
    workspace = lem.project.workspace if lem.project_id else None
    # The link searches the number without its revision ("26-000062"), so it still finds the ticket once it's
    # revised again.
    return notify(recipients, actor, kind, title, message, f"/field-tickets?ticket={lem.ticket_number_for(0)}",
                  workspace, "field_ticket", lem.id)
