"""
Expert Report: a live overview, cost profitability (admins only) and a payroll export, over one set of filters.

    GET /api/reports/expert/options/          filter choices: jobs, clients, employees
    GET /api/reports/expert/overview/         tickets in, approvals outstanding, uninvoiced work, overhead
    GET /api/reports/expert/profitability/    rate vs cost of the crew rates and equipment charged, by job / employee / item
    GET /api/reports/expert/payroll/          hours per employee per day, straight time / overtime
    GET /api/reports/expert/payroll/export/   the same as an Excel workbook

Filters (query string): workspace, date_from, date_to (YYYY-MM-DD, Mountain Time days), project, client, employee
(comma-separated ids), work=billable|non_billable, approved=1 (approved time only).

Money comes from the rate sheet (Rates page). Every rate or equipment item charged on a time entry (AssetUsage) is
billed at the item's rate (usage.cost) and costs the company the item's cost (OrganizationAsset.cost_rate: the
employee's wages for a crew rate such as OPFC, the running cost for equipment) times the same quantity. Rate minus
cost is the margin.

Overtime (no double time): a person's hours over 8 in a day or 44 in a Sunday-Saturday week. It is worked out on
everything the person logged that week (approved time only when `approved` is set), whichever job it was on, so a
job filter never moves where overtime starts.
"""

import io
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import TimeEntryApproval
from approvals.utils import get_week_bounds
from clients.models import Client
from core.utils.envision_time import envision_day_bounds_utc, utc_to_envision_local
from projects.models import Project
from time_entries.models import TimeEntry
from workspaces.models import WorkspaceMember
from workspaces.permissions import ADMIN_ONLY, MANAGE_ROLES, has_workspace_role

from .models import LEMReport

DAILY_ST_HOURS = Decimal("8")
WEEKLY_ST_HOURS = Decimal("44")
FIELD_TICKET_PREFIX = "FTF-"
TICKET_STATUSES = ("approved", "invoiced", "pending", "rejected")
ZERO = Decimal("0")


class ExpertReportError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _money(value):
    return round(float(value or 0), 2)


def _hours(value):
    return round(float(value or 0), 2)


def _ids(raw):
    return [part for part in (raw or "").split(",") if part.strip()]


def _parse_day(raw, fallback):
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ExpertReportError("Dates must be YYYY-MM-DD.") from exc


def _ticket_amount(lem):
    """A ticket's printed total: labour + equipment on a BOSS template, equipment only on older ones."""
    data = lem.report_data or {}
    raw = (data.get("boss") or {}).get("subtotal") or data.get("total_cost") or "0"
    try:
        return Decimal(str(raw).replace("$", "").replace(",", "").strip() or "0")
    except ArithmeticError:
        return ZERO


def usage_quantity(usage, entry):
    """Hours or units charged: what was recorded, else the entry's hours for an hourly item."""
    if usage.quantity_used is not None:
        return Decimal(usage.quantity_used)
    if usage.asset.charge_type == "hourly":
        return Decimal(entry.duration) / 60
    return ZERO


def usage_cost(usage, entry):
    """What the charge costs the company (cost rate x quantity), or None when the item has no cost yet."""
    rate = usage.asset.cost_rate
    return None if rate is None else Decimal(rate) * usage_quantity(usage, entry)


class Filters:
    """The report's filters, read once from the query string, and the querysets they narrow."""

    def __init__(self, request, roles):
        user = request.user
        workspace_id = request.query_params.get("workspace")
        memberships = WorkspaceMember.objects.filter(user=user, is_deleted=False, is_active=True)
        if workspace_id:
            if not (user.is_superuser or memberships.filter(workspace_id=workspace_id).exists()):
                raise ExpertReportError("Workspace not found.", 404)
        else:
            membership = memberships.filter(role__in=roles).first() or memberships.first()
            if not membership:
                raise ExpertReportError("You are not in a workspace.", 403)
            workspace_id = membership.workspace_id
        if not has_workspace_role(user, workspace_id, roles):
            raise ExpertReportError("You do not have access to this report.", 403)
        self.workspace_id = workspace_id

        week_start, week_end = get_week_bounds(timezone.localdate())
        self.date_from = _parse_day(request.query_params.get("date_from"), week_start)
        self.date_to = _parse_day(request.query_params.get("date_to"), week_end)
        if self.date_to < self.date_from:
            raise ExpertReportError("date_to is before date_from.")
        if (self.date_to - self.date_from).days > 366:
            raise ExpertReportError("Pick a period of a year or less.")

        params = request.query_params
        self.projects = _ids(params.get("project"))
        self.clients = _ids(params.get("client"))
        self.employees = _ids(params.get("employee"))
        self.work = (params.get("work") or "").strip().lower()
        self.approved_only = (params.get("approved") or "").lower() in ("1", "true", "yes")

    # ── time entries ─────────────────────────────────────────────────────
    def _base_entries(self, start, end, people, approved=None):
        lo, _ = envision_day_bounds_utc(start)
        _, hi = envision_day_bounds_utc(end)
        qs = TimeEntry.objects.filter(
            workspace_id=self.workspace_id, is_deleted=False, start_time__gte=lo, start_time__lte=hi,
        )
        if people:
            qs = qs.filter(user_id__in=people)
        if self.approved_only if approved is None else approved:
            qs = qs.filter(
                timeentryapprovalitem__status="approved", timeentryapprovalitem__is_deleted=False,
            ).distinct()
        return qs

    def entries(self, use_work=True, approved=None):
        """Entries in the period matching every filter (the work filter optional; `approved` overrides the
        approved-only filter)."""
        qs = self._base_entries(self.date_from, self.date_to, self.employees, approved)
        if self.projects:
            qs = qs.filter(project_id__in=self.projects)
        if self.clients:
            qs = qs.filter(project__client_id__in=self.clients)
        if use_work and self.work == "billable":
            qs = qs.filter(billable=True).filter(Q(project__isnull=True) | Q(project__billable=True))
        elif use_work and self.work == "non_billable":
            qs = qs.filter(Q(billable=False) | Q(project__billable=False))
        return qs.select_related("user", "project__client", "job_title", "task").prefetch_related("asset_usages__asset")

    def split(self, entries):
        """{entry id: (straight hours, overtime hours)} for `entries`, counting every hour their people logged
        from the start of the first week, on any job, toward the 8 h / 44 h limits."""
        users = {e.user_id for e in entries}
        if not users:
            return {}
        week_start = get_week_bounds(self.date_from)[0]
        everything = (
            self._base_entries(week_start, self.date_to, users)
            .only("id", "user_id", "start_time", "duration")
            .order_by("user_id", "start_time", "id")
        )
        daily = defaultdict(lambda: ZERO)
        weekly = defaultdict(lambda: ZERO)
        result = {}
        for entry in everything:
            day = utc_to_envision_local(entry.start_time).date()
            week = get_week_bounds(day)[0]
            hours = Decimal(entry.duration) / 60
            straight = min(hours, max(DAILY_ST_HOURS - daily[(entry.user_id, day)], ZERO))
            straight = min(straight, max(WEEKLY_ST_HOURS - weekly[(entry.user_id, week)], ZERO))
            daily[(entry.user_id, day)] += straight
            weekly[(entry.user_id, week)] += straight
            result[entry.id] = (straight, hours - straight)
        return result

    # ── field tickets ────────────────────────────────────────────────────
    def tickets(self):
        qs = LEMReport.objects.filter(
            project__workspace_id=self.workspace_id, lem_number__startswith=FIELD_TICKET_PREFIX,
        )
        if self.projects:
            qs = qs.filter(project_id__in=self.projects)
        if self.clients:
            qs = qs.filter(project__client_id__in=self.clients)
        if self.employees:
            qs = qs.filter(time_entries__user_id__in=self.employees).distinct()
        return qs


class _ExpertView(APIView):
    permission_classes = [IsAuthenticated]
    roles = MANAGE_ROLES

    def get(self, request):
        try:
            filters = Filters(request, self.roles)
            return self.build(request, filters)
        except ExpertReportError as exc:
            return Response({"error": str(exc)}, status=exc.status)

    def build(self, request, filters):  # pragma: no cover - overridden
        raise NotImplementedError


class ExpertOptionsView(_ExpertView):
    """What the filter dropdowns offer."""

    def build(self, request, filters):
        ws = filters.workspace_id
        members = (
            WorkspaceMember.objects.filter(workspace_id=ws, is_deleted=False, is_active=True)
            .select_related("user").order_by("user__first_name", "user__last_name")
        )
        return Response({
            "workspace": str(ws),
            "is_admin": has_workspace_role(request.user, ws, ADMIN_ONLY),
            "projects": [
                {"id": str(p.id), "name": p.name, "code": p.job_code or "", "client_id": str(p.client_id) if p.client_id else None,
                 "billable": p.billable}
                for p in Project.objects.filter(workspace_id=ws).order_by("name")
            ],
            "clients": [{"id": str(c.id), "name": c.name} for c in Client.objects.filter(workspace_id=ws).order_by("name")],
            "employees": [{"id": str(m.user_id), "name": m.user.get_full_name() or m.user.email} for m in members],
        })


class ExpertOverviewView(_ExpertView):
    """Tickets in, approvals outstanding, uninvoiced work and overhead for the period."""

    def build(self, request, filters):
        today = timezone.localdate()
        days = (filters.date_to - filters.date_from).days + 1

        # Tickets in: field tickets dated in the period, by status and day; and the same length of time before.
        tickets = filters.tickets()
        in_period = list(tickets.filter(lem_date__range=(filters.date_from, filters.date_to)).values("lem_date", "status"))
        before = tickets.filter(
            lem_date__range=(filters.date_from - timedelta(days=days), filters.date_from - timedelta(days=1))
        ).count()
        empty = {status: 0 for status in TICKET_STATUSES}
        by_day = {filters.date_from + timedelta(days=i): dict(empty) for i in range(days)}
        status_counts = dict(empty)
        for row in in_period:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            if row["lem_date"] in by_day:
                by_day[row["lem_date"]][row["status"]] += 1

        # Approvals outstanding: submitted weeks, oldest first.
        approvals = TimeEntryApproval.objects.filter(
            workspace_id=filters.workspace_id, status="submitted", is_deleted=False,
        ).select_related("user")
        if filters.employees:
            approvals = approvals.filter(user_id__in=filters.employees)
        approvals = approvals.annotate(
            minutes=Sum("items__time_entry__duration", filter=Q(items__is_deleted=False, items__time_entry__is_deleted=False)),
        ).order_by("start_date", "created_at")
        approval_rows = [
            {
                "id": str(a.id),
                "employee": a.user.get_full_name() or a.user.email,
                "start_date": a.start_date,
                "end_date": a.end_date,
                "hours": _hours(Decimal(a.minutes or 0) / 60),
                "waiting_days": max((today - timezone.localtime(a.created_at).date()).days, 0),
            }
            for a in approvals
        ]

        # Uninvoiced work: approved (not yet invoiced) tickets up to the end of the period, billable jobs only.
        uninvoiced = (
            tickets.filter(status=LEMReport.STATUS_APPROVED, lem_date__lte=filters.date_to, project__billable=True)
            .select_related("project__client")
        )
        by_client = {}
        for lem in uninvoiced:
            client = lem.project.client
            key = str(client.id) if client else ""
            row = by_client.setdefault(key, {"client_id": key or None, "name": client.name if client else "No client",
                                             "amount": ZERO, "tickets": 0, "oldest": lem.lem_date})
            row["amount"] += _ticket_amount(lem)
            row["tickets"] += 1
            if lem.lem_date and (row["oldest"] is None or lem.lem_date < row["oldest"]):
                row["oldest"] = lem.lem_date
        clients = sorted(by_client.values(), key=lambda r: r["amount"], reverse=True)

        # Overhead: non-billable time, at the cost of the crew rates and equipment charged on it.
        total_hours = ZERO
        overhead = {}
        for entry in filters.entries(use_work=False):
            hours = Decimal(entry.duration) / 60
            total_hours += hours
            if entry.is_billable_to_client:
                continue
            name = entry.project.name if entry.project else "No project"
            row = overhead.setdefault(name, {"name": name, "hours": ZERO, "cost": ZERO, "people": set(), "uncosted_hours": ZERO})
            row["hours"] += hours
            row["people"].add(entry.user_id)
            costs = [usage_cost(usage, entry) for usage in entry.asset_usages.all()]
            if not costs or any(cost is None for cost in costs):
                row["uncosted_hours"] += hours
            row["cost"] += sum((cost for cost in costs if cost is not None), ZERO)
        overhead_rows = sorted(overhead.values(), key=lambda r: (r["cost"], r["hours"]), reverse=True)
        overhead_hours = sum((r["hours"] for r in overhead_rows), ZERO)

        return Response({
            "period": {"date_from": filters.date_from, "date_to": filters.date_to},
            "tickets": {
                "total": len(in_period),
                "previous_total": before,
                "by_status": status_counts,
                "by_day": [{"date": day, **counts} for day, counts in sorted(by_day.items())],
            },
            "approvals": {
                "weeks": len(approval_rows),
                "hours": _hours(sum(r["hours"] for r in approval_rows)),
                "oldest_waiting_days": max((r["waiting_days"] for r in approval_rows), default=0),
                "rows": approval_rows[:10],
            },
            "uninvoiced": {
                "amount": _money(sum((r["amount"] for r in clients), ZERO)),
                "tickets": sum(r["tickets"] for r in clients),
                "by_client": [{**r, "amount": _money(r["amount"])} for r in clients],
            },
            "overhead": {
                "hours": _hours(overhead_hours),
                "cost": _money(sum((r["cost"] for r in overhead_rows), ZERO)),
                "share_of_hours": _money(overhead_hours / total_hours * 100) if total_hours else 0,
                "uncosted_hours": _hours(sum((r["uncosted_hours"] for r in overhead_rows), ZERO)),
                "rows": [
                    {"name": r["name"], "hours": _hours(r["hours"]), "cost": _money(r["cost"]), "people": len(r["people"]),
                     "uncosted_hours": _hours(r["uncosted_hours"])}
                    for r in overhead_rows
                ],
            },
        })


class ExpertProfitabilityView(_ExpertView):
    """Rate vs cost of the crew rates and equipment charged on billable work, by job, employee and item. Admins only."""

    roles = ADMIN_ONLY

    def build(self, request, filters):
        jobs = {}
        people = {}
        items = {}
        missing_cost = {}
        totals = {"billed": ZERO, "cost": ZERO, "billed_rates": ZERO, "billed_equipment": ZERO}
        hours = ZERO
        unpriced_hours = ZERO

        def bucket(store, key, seed):
            return store.setdefault(key, {**seed, "billed": ZERO, "cost": ZERO, "quantity": ZERO, "hours": ZERO, "_entries": set()})

        for entry in filters.entries(use_work=False):
            if not entry.is_billable_to_client:
                continue
            entry_hours = Decimal(entry.duration) / 60
            hours += entry_hours
            usages = list(entry.asset_usages.all())
            if not usages:
                unpriced_hours += entry_hours
            project = entry.project
            for usage in usages:
                asset = usage.asset
                quantity = usage_quantity(usage, entry)
                billed = Decimal(usage.cost or 0)
                cost = usage_cost(usage, entry)
                if cost is None:
                    missing = missing_cost.setdefault(asset.id, {"name": asset.name, "billed": ZERO})
                    missing["billed"] += billed
                    cost = ZERO
                totals["billed"] += billed
                totals["cost"] += cost
                totals["billed_equipment" if asset.is_equipment else "billed_rates"] += billed
                rows = (
                    bucket(jobs, project.id if project else None, {
                        "project_id": str(project.id) if project else None,
                        "name": project.name if project else "No project",
                        "code": (project.job_code or "") if project else "",
                        "client": project.client.name if project and project.client else "",
                    }),
                    bucket(people, entry.user_id, {
                        "user_id": str(entry.user_id), "name": entry.user.get_full_name() or entry.user.email,
                    }),
                    bucket(items, asset.id, {
                        "asset_id": str(asset.id), "name": asset.name,
                        "kind": "equipment" if asset.is_equipment else "rate",
                        "unit": "h" if asset.charge_type == "hourly" else "unit",
                        "rate": _money(asset.hourly_rate if asset.charge_type == "hourly" else asset.quantity_rate),
                        "cost_rate": _money(asset.cost_rate) if asset.cost_rate is not None else None,
                    }),
                )
                for row in rows:
                    row["billed"] += billed
                    row["cost"] += cost
                    row["quantity"] += quantity
                    if entry.id not in row["_entries"]:
                        row["_entries"].add(entry.id)
                        row["hours"] += entry_hours

        def finish(row, keep_quantity=False):
            margin = row["billed"] - row["cost"]
            out = {k: v for k, v in row.items() if k not in ("billed", "cost", "quantity", "hours", "_entries")}
            out.update({
                "hours": _hours(row["hours"]), "billed": _money(row["billed"]), "cost": _money(row["cost"]),
                "margin": _money(margin), "margin_pct": _money(margin / row["billed"] * 100) if row["billed"] else None,
            })
            if keep_quantity:
                out["quantity"] = _hours(row["quantity"])
            return out

        margin = totals["billed"] - totals["cost"]
        return Response({
            "period": {"date_from": filters.date_from, "date_to": filters.date_to},
            "summary": {
                "hours": _hours(hours),
                "billed": _money(totals["billed"]),
                "billed_rates": _money(totals["billed_rates"]),
                "billed_equipment": _money(totals["billed_equipment"]),
                "cost": _money(totals["cost"]),
                "margin": _money(margin),
                "margin_pct": _money(margin / totals["billed"] * 100) if totals["billed"] else None,
                "unpriced_hours": _hours(unpriced_hours),
                "missing_cost": sorted(
                    ({"name": m["name"], "billed": _money(m["billed"])} for m in missing_cost.values()),
                    key=lambda m: m["billed"], reverse=True,
                ),
            },
            "by_job": sorted((finish(r) for r in jobs.values()), key=lambda r: r["billed"], reverse=True),
            "by_employee": sorted((finish(r) for r in people.values()), key=lambda r: r["billed"], reverse=True),
            "by_item": sorted((finish(r, keep_quantity=True) for r in items.values()), key=lambda r: r["billed"], reverse=True),
        })


def payroll_data(filters):
    """Rows of hours per employee per day for the period, straight time / overtime."""
    entries = list(filters.entries(use_work=False))
    split = filters.split(entries)
    days = [filters.date_from + timedelta(days=i) for i in range((filters.date_to - filters.date_from).days + 1)]
    rows = {}
    for entry in entries:
        hours = Decimal(entry.duration) / 60
        straight, overtime = split.get(entry.id, (hours, ZERO))
        day = utc_to_envision_local(entry.start_time).date()
        row = rows.setdefault(entry.user_id, {
            "user_id": str(entry.user_id),
            "name": entry.user.get_full_name() or entry.user.email,
            "titles": defaultdict(lambda: ZERO),
            "days": defaultdict(lambda: {"st": ZERO, "ot": ZERO, "jobs": set()}),
        })
        cell = row["days"][day]
        cell["st"] += straight
        cell["ot"] += overtime
        if entry.project:
            cell["jobs"].add(entry.project.job_code or entry.project.name)
        row["titles"][entry.job_title.name if entry.job_title else ""] += hours

    out = []
    for row in sorted(rows.values(), key=lambda r: r["name"]):
        st = sum((c["st"] for c in row["days"].values()), ZERO)
        ot = sum((c["ot"] for c in row["days"].values()), ZERO)
        out.append({
            "user_id": row["user_id"],
            "name": row["name"],
            "job_title": max(row["titles"].items(), key=lambda kv: kv[1])[0] if row["titles"] else "",
            "days": [
                {"date": day, "st": _hours(row["days"][day]["st"]), "ot": _hours(row["days"][day]["ot"]),
                 "jobs": sorted(row["days"][day]["jobs"])}
                if day in row["days"] else {"date": day, "st": 0, "ot": 0, "jobs": []}
                for day in days
            ],
            "st_hours": _hours(st), "ot_hours": _hours(ot), "total_hours": _hours(st + ot),
        })

    pending = {"entries": 0, "hours": 0}
    if filters.approved_only:
        # Time in the period that isn't approved yet, so isn't in this payroll.
        waiting = filters.entries(use_work=False, approved=False).exclude(id__in=[e.id for e in entries])
        pending = {"entries": waiting.count(), "hours": _hours(Decimal(waiting.aggregate(m=Sum("duration"))["m"] or 0) / 60)}

    return {
        "period": {"date_from": filters.date_from, "date_to": filters.date_to},
        "days": days,
        "rows": out,
        "totals": {
            "st_hours": _hours(sum(r["st_hours"] for r in out)),
            "ot_hours": _hours(sum(r["ot_hours"] for r in out)),
            "total_hours": _hours(sum(r["total_hours"] for r in out)),
            "employees": len(out),
        },
        "not_approved": pending,
        "rules": {"daily_st_hours": float(DAILY_ST_HOURS), "weekly_st_hours": float(WEEKLY_ST_HOURS)},
    }


class ExpertPayrollView(_ExpertView):
    def build(self, request, filters):
        return Response(payroll_data(filters))


class ExpertPayrollExportView(_ExpertView):
    """The payroll as an Excel workbook: one row per employee per day worked, and a totals sheet."""

    def build(self, request, filters):
        from openpyxl import Workbook
        from openpyxl.styles import Font

        data = payroll_data(filters)
        book = Workbook()
        daily = book.active
        daily.title = "Daily hours"
        daily.append(["Employee", "Job title", "Date", "Jobs", "Straight time", "Overtime", "Total"])
        for row in data["rows"]:
            for day in row["days"]:
                if day["st"] or day["ot"]:
                    daily.append([row["name"], row["job_title"], day["date"], ", ".join(day["jobs"]),
                                  day["st"], day["ot"], round(day["st"] + day["ot"], 2)])
        totals = book.create_sheet("Totals")
        totals.append(["Employee", "Job title", "Straight time", "Overtime", "Total"])
        for row in data["rows"]:
            totals.append([row["name"], row["job_title"], row["st_hours"], row["ot_hours"], row["total_hours"]])
        totals.append(["All employees", "", data["totals"]["st_hours"], data["totals"]["ot_hours"], data["totals"]["total_hours"]])
        for sheet in (daily, totals):
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = max(12, min(40, max(len(str(c.value or "")) for c in column) + 2))
        for cell in daily["C"][1:]:
            cell.number_format = "yyyy-mm-dd"

        buffer = io.BytesIO()
        book.save(buffer)
        name = f"Payroll_{filters.date_from:%Y-%m-%d}_to_{filters.date_to:%Y-%m-%d}.xlsx"
        response = HttpResponse(buffer.getvalue(),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
