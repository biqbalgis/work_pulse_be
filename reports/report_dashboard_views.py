"""
Report Dashboard (Clockify-style "Time report") API.

POST /api/reports/report-dashboard/summary/   compact facts for the Summary / Weekly tabs
POST /api/reports/report-dashboard/entries/   paged list of individual entries (Detailed tab)

Body (both):
    date_from, date_to   (str, required)  YYYY-MM-DD, at most 366 days apart
    user_ids      (list)   employees
    groups        (list)   user groups (subcontractors | external_envision | internal_envision)
    client_ids    (list)
    project_ids   (list)
    task_ids      (list)
    billable      (str)    "billable" | "non_billable" | omitted = both
    statuses      (list)   "approved" | "pending" | "unsubmitted"
    description   (str)    text the description must contain
    workspace     (uuid)   superusers: narrow to one workspace
entries also takes page / page_size.

Same access rule as the Custom Report (superusers, or workspace members whose
"custom_report" permission is on). Days are Mountain-time days (Envision), like
the other Envision reports. Nothing is stored.

The summary returns *facts* — one row per (day, employee, project, task,
billable, description) with minutes / entry count / amount — plus lookup tables.
The page does the grouping, charts and rounding from those, so changing "group
by" or the chart split doesn't need another request.
"""

from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Exists, OuterRef, Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import TimeEntryApprovalItem
from clients.models import Client
from core.utils.envision_time import envision_day_bounds_utc, utc_to_envision_local
from projects.models import Project
from tasks.models import Task
from time_entries.models import TimeEntry
from users.models import User
from workspaces.models import WorkspaceMember

from .custom_report_utils import MAX_RANGE_DAYS
from .custom_report_views import _BadRequest, _as_list, _parse_date, _resolve_workspace_scope

MAX_FACTS = 40000
MAX_DESCRIPTION_CHARS = 300
STATUS_CHOICES = ("approved", "pending", "unsubmitted")
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _filtered_entries(request):
    """Validate the body and return (queryset, date_from, date_to, workspace_ids) or a Response."""
    data = request.data
    scope = _resolve_workspace_scope(request, data.get("workspace"))
    if isinstance(scope, Response):
        return scope
    workspace_ids = scope

    date_from = _parse_date(data.get("date_from"), "date_from")
    date_to = _parse_date(data.get("date_to"), "date_to")
    if date_to < date_from:
        raise _BadRequest("date_to must be on or after date_from")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise _BadRequest(f"Date range can be at most {MAX_RANGE_DAYS} days")

    user_ids = _as_list(data.get("user_ids"), "user_ids")
    groups = _as_list(data.get("groups"), "groups")
    client_ids = _as_list(data.get("client_ids"), "client_ids")
    project_ids = _as_list(data.get("project_ids"), "project_ids")
    task_ids = _as_list(data.get("task_ids"), "task_ids")
    statuses = _as_list(data.get("statuses"), "statuses")
    billable = data.get("billable") or None
    description = (data.get("description") or "").strip()

    valid_groups = {value for value, _label in WorkspaceMember.GROUP_CHOICES}
    if not set(groups) <= valid_groups:
        raise _BadRequest("groups must be a list of: " + ", ".join(sorted(valid_groups)))
    if not set(statuses) <= set(STATUS_CHOICES):
        raise _BadRequest("statuses must be a list of: " + ", ".join(STATUS_CHOICES))
    if billable not in (None, "billable", "non_billable"):
        raise _BadRequest('billable must be "billable" or "non_billable"')

    start_utc, _ = envision_day_bounds_utc(date_from)
    _, end_utc = envision_day_bounds_utc(date_to)
    qs = TimeEntry.objects.filter(is_deleted=False, start_time__gte=start_utc, start_time__lte=end_utc)
    if workspace_ids is not None:
        qs = qs.filter(workspace_id__in=workspace_ids)

    if user_ids:
        qs = qs.filter(user_id__in=user_ids)
    if groups:
        members = WorkspaceMember.objects.filter(group__in=groups)
        if workspace_ids is not None:
            members = members.filter(workspace_id__in=workspace_ids)
        qs = qs.filter(user_id__in=members.values("user_id"))
    if client_ids:
        qs = qs.filter(project__client_id__in=client_ids)
    if project_ids:
        qs = qs.filter(project_id__in=project_ids)
    if task_ids:
        qs = qs.filter(task_id__in=task_ids)
    if billable == "billable":
        qs = qs.filter(billable=True)
    elif billable == "non_billable":
        qs = qs.filter(billable=False)
    if description:
        qs = qs.filter(description__icontains=description)
    if statuses:
        items = TimeEntryApprovalItem.objects.filter(time_entry=OuterRef("pk"))
        any_item = Exists(items)
        status_q = Q()
        if "approved" in statuses:
            status_q |= Q(Exists(items.filter(approval__status="approved")))
        if "pending" in statuses:
            status_q |= Q(Exists(items.exclude(approval__status="approved")))
        if "unsubmitted" in statuses:
            status_q |= ~Q(any_item)
        qs = qs.filter(status_q)
    return qs, date_from, date_to, workspace_ids


def _full_name(user):
    return user.get_full_name() or user.email


class _ReportDashboardBase(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            return self.build(request)
        except _BadRequest as exc:
            return Response({"error": str(exc)}, status=400)
        except DjangoValidationError:
            return Response({"error": "A filter contains an invalid id"}, status=400)


class ReportDashboardSummaryView(_ReportDashboardBase):
    def build(self, request):
        result = _filtered_entries(request)
        if isinstance(result, Response):
            return result
        qs, date_from, date_to, workspace_ids = result

        facts = defaultdict(lambda: [0, 0, 0.0])  # key -> [minutes, entries, amount]
        for start, user_id, project_id, task_id, billable, description, duration, cost in qs.values_list(
            "start_time", "user_id", "project_id", "task_id", "billable", "description", "duration", "cost"
        ).iterator():
            minutes = duration or 0
            if minutes <= 0:
                continue
            day = utc_to_envision_local(start).date()
            if not (date_from <= day <= date_to):
                continue
            key = (
                day.isoformat(), str(user_id), str(project_id) if project_id else None,
                str(task_id) if task_id else None, bool(billable), (description or "")[:MAX_DESCRIPTION_CHARS],
            )
            fact = facts[key]
            fact[0] += minutes
            fact[1] += 1
            if billable:
                fact[2] += float(cost or 0)
            if len(facts) > MAX_FACTS:
                raise _BadRequest("That's too much data for one report — narrow the date range or add a filter.")

        project_ids = {k[2] for k in facts if k[2]}
        task_ids = {k[3] for k in facts if k[3]}
        user_ids = {k[1] for k in facts}

        projects = {
            str(p.id): {"name": p.name, "job_code": p.job_code or "", "color": p.color or None,
                        "client_id": str(p.client_id) if p.client_id else None}
            for p in Project.objects.filter(id__in=project_ids)
        }
        client_ids = {p["client_id"] for p in projects.values() if p["client_id"]}
        clients = {str(c.id): {"name": c.name} for c in Client.objects.filter(id__in=client_ids)}
        tasks = {str(t.id): {"name": t.name} for t in Task.objects.filter(id__in=task_ids)}

        group_of = {}
        members = WorkspaceMember.objects.filter(user_id__in=user_ids)
        if workspace_ids is not None:
            members = members.filter(workspace_id__in=workspace_ids)
        for uid, grp in members.exclude(group__isnull=True).values_list("user_id", "group"):
            group_of.setdefault(str(uid), grp)
        users = {
            str(u.id): {"name": _full_name(u), "group": group_of.get(str(u.id))}
            for u in User.all_objects.filter(id__in=user_ids)
        }

        rows = [
            {"d": d, "u": u, "p": p, "t": t, "b": b, "desc": desc, "m": m, "n": n, "c": round(c, 2)}
            for (d, u, p, t, b, desc), (m, n, c) in sorted(facts.items(), key=lambda kv: kv[0])
        ]
        return Response({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "lookups": {"projects": projects, "clients": clients, "tasks": tasks, "users": users},
            "facts": rows,
            "totals": {
                "minutes": sum(r["m"] for r in rows),
                "billable_minutes": sum(r["m"] for r in rows if r["b"]),
                "amount": round(sum(r["c"] for r in rows), 2),
                "entries": sum(r["n"] for r in rows),
            },
        })


class ReportDashboardEntriesView(_ReportDashboardBase):
    def build(self, request):
        result = _filtered_entries(request)
        if isinstance(result, Response):
            return result
        qs, _date_from, _date_to, _workspace_ids = result

        try:
            page = max(int(request.data.get("page") or 1), 1)
            page_size = min(max(int(request.data.get("page_size") or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
        except (TypeError, ValueError):
            raise _BadRequest("page and page_size must be numbers")

        qs = qs.filter(duration__gt=0).select_related("user", "project", "project__client", "task").order_by(
            "-start_time", "-created_at"
        )
        count = qs.count()
        offset = (page - 1) * page_size
        results = []
        for e in qs[offset: offset + page_size]:
            start_local = utc_to_envision_local(e.start_time)
            end_local = utc_to_envision_local(e.end_time) if e.end_time else None
            results.append({
                "id": str(e.id),
                "date": start_local.date().isoformat(),
                "start": start_local.strftime("%H:%M"),
                "end": end_local.strftime("%H:%M") if end_local else None,
                "user": _full_name(e.user),
                "project": e.project.name if e.project else None,
                "job_code": (e.project.job_code or "") if e.project else "",
                "client": e.project.client.name if e.project and e.project.client else None,
                "task": e.task.name if e.task else None,
                "description": e.description or "",
                "minutes": e.duration,
                "billable": bool(e.billable),
                "amount": round(float(e.cost or 0), 2) if e.billable else 0.0,
            })
        return Response({"count": count, "page": page, "page_size": page_size, "results": results})
