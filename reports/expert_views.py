"""
Expert Report: a live overview, cost profitability (admins only) and a payroll export, over one set of filters.

    GET /api/reports/expert/options/          filter choices: crews, jobs, clients, employees
    GET /api/reports/expert/overview/         tickets in, approvals outstanding, uninvoiced work, overhead
    GET /api/reports/expert/profitability/    billed (bill rates) vs wage cost (wage rates) by job and employee
    GET /api/reports/expert/payroll/          hours per employee per day, straight time / overtime
    GET /api/reports/expert/payroll/export/   the same as an Excel workbook

Filters (query string): workspace, date_from, date_to (YYYY-MM-DD, Mountain Time days), crew, project, client,
employee (comma-separated ids), work=billable|non_billable, approved=1 (approved time only).

Overtime (no double time): a person's hours over 8 in a day or 44 in a Sunday-Saturday week. It is worked out on
everything the person logged that week (approved time only when `approved` is set), whichever job it was on, so a
job filter never moves where overtime starts.

Wage cost: the employee's own wage rate (WorkspaceMember.wage_rate) if set, overtime at 1.5x; otherwise the labour
rate of the job title they worked as (LaborRate "Day": regular / overtime cost). Billed: the entry's cost at the
job's bill rate (TimeEntry.cost), billable work only.
"""

import io
from collections import defaultdict
from datetime import date, datetime, timedelta
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
from projects.models import LaborRate, Project
from time_entries.models import TimeEntry
from workspaces.models import Crew, WorkspaceMember
from workspaces.permissions import ADMIN_ONLY, MANAGE_ROLES, has_workspace_role

from .models import LEMReport

DAILY_ST_HOURS = Decimal("8")
WEEKLY_ST_HOURS = Decimal("44")
OT_MULTIPLIER = Decimal("1.5")
FIELD_TICKET_PREFIX = "FTF-"
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

        today = timezone.localdate()
        week_start, week_end = get_week_bounds(today)
        self.date_from = _parse_day(request.query_params.get("date_from"), week_start)
        self.date_to = _parse_day(request.query_params.get("date_to"), week_end)
        if self.date_to < self.date_from:
            raise ExpertReportError("date_to is before date_from.")
        if (self.date_to - self.date_from).days > 366:
            raise ExpertReportError("Pick a period of a year or less.")

        params = request.query_params
        self.crews = _ids(params.get("crew"))
        self.projects = _ids(params.get("project"))
        self.clients = _ids(params.get("client"))
        self.employees = _ids(params.get("employee"))
        self.work = (params.get("work") or "").strip().lower()
        self.approved_only = (params.get("approved") or "").lower() in ("1", "true", "yes")

    # ── people ───────────────────────────────────────────────────────────
    def people(self):
        """User ids the crew / employee filters allow (None = everyone)."""
        allowed = None
        if self.crews:
            allowed = set(
                WorkspaceMember.objects.filter(workspace_id=self.workspace_id, crew_id__in=self.crews, is_deleted=False)
                .values_list("user_id", flat=True)
            )
        if self.employees:
            chosen = {str(e) for e in self.employees}
            allowed = {u for u in allowed if str(u) in chosen} if allowed is not None else set(self.employees)
        return allowed

    # ── time entries ─────────────────────────────────────────────────────
    def _base_entries(self, start, end, people, approved=None):
        lo, _ = envision_day_bounds_utc(start)
        _, hi = envision_day_bounds_utc(end)
        qs = TimeEntry.objects.filter(
            workspace_id=self.workspace_id, is_deleted=False, start_time__gte=lo, start_time__lte=hi,
        )
        if people is not None:
            qs = qs.filter(user_id__in=people)
        if self.approved_only if approved is None else approved:
            qs = qs.filter(
                timeentryapprovalitem__status="approved", timeentryapprovalitem__is_deleted=False,
            ).distinct()
        return qs

    def entries(self, use_work=True, approved=None):
        """Entries in the period matching every filter (the work filter optional; `approved` overrides the
        approved-only filter)."""
        qs = self._base_entries(self.date_from, self.date_to, self.people(), approved)
        if self.projects:
            qs = qs.filter(project_id__in=self.projects)
        if self.clients:
            qs = qs.filter(project__client_id__in=self.clients)
        if use_work and self.work == "billable":
            qs = qs.filter(billable=True).filter(Q(project__isnull=True) | Q(project__billable=True))
        elif use_work and self.work == "non_billable":
            qs = qs.filter(Q(billable=False) | Q(project__billable=False))
        return qs.select_related("user", "project__client", "job_title", "task")

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
        people = self.people()
        if people is not None:
            qs = qs.filter(time_entries__user_id__in=people).distinct()
        return qs


class WageRates:
    """Wage per hour for an entry: the employee's own rate, else their job title's labour rate."""

    def __init__(self, workspace_id):
        self.members = {
            m.user_id: m.wage_rate
            for m in WorkspaceMember.objects.filter(workspace_id=workspace_id, is_deleted=False).only("user_id", "wage_rate")
        }
        self.titles = {
            rate.job_title_id: (rate.regular_cost, rate.overtime_cost)
            for rate in LaborRate.objects.filter(job_title__workspace_id=workspace_id, condition="Day", is_deleted=False)
        }

    def for_entry(self, entry):
        """(straight rate, overtime rate, source) — source "employee", "job title" or None when unknown."""
        own = self.members.get(entry.user_id)
        if own is not None:
            return Decimal(own), Decimal(own) * OT_MULTIPLIER, "employee"
        title = self.titles.get(entry.job_title_id)
        if title and title[0] is not None:
            regular = Decimal(title[0])
            overtime = Decimal(title[1]) if title[1] else regular * OT_MULTIPLIER
            return regular, overtime, "job title"
        return None, None, None


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
            .select_related("user", "crew").order_by("user__first_name", "user__last_name")
        )
        return Response({
            "workspace": str(ws),
            "is_admin": has_workspace_role(request.user, ws, ADMIN_ONLY),
            "crews": [{"id": str(c.id), "name": c.name} for c in Crew.objects.filter(workspace_id=ws)],
            "projects": [
                {"id": str(p.id), "name": p.name, "code": p.job_code or "", "client_id": str(p.client_id) if p.client_id else None,
                 "billable": p.billable}
                for p in Project.objects.filter(workspace_id=ws).order_by("name")
            ],
            "clients": [{"id": str(c.id), "name": c.name} for c in Client.objects.filter(workspace_id=ws).order_by("name")],
            "employees": [
                {"id": str(m.user_id), "name": m.user.get_full_name() or m.user.email,
                 "crew_id": str(m.crew_id) if m.crew_id else None}
                for m in members
            ],
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
        by_day = {filters.date_from + timedelta(days=i): {"approved": 0, "pending": 0, "rejected": 0} for i in range(days)}
        status_counts = {"approved": 0, "pending": 0, "rejected": 0}
        for row in in_period:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            if row["lem_date"] in by_day:
                by_day[row["lem_date"]][row["status"]] += 1

        # Approvals outstanding: submitted weeks, oldest first.
        approvals = TimeEntryApproval.objects.filter(
            workspace_id=filters.workspace_id, status="submitted", is_deleted=False,
        ).select_related("user")
        people = filters.people()
        if people is not None:
            approvals = approvals.filter(user_id__in=people)
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

        # Uninvoiced work: approved tickets up to the end of the period without an invoice (billable jobs only).
        uninvoiced = (
            tickets.filter(status=LEMReport.STATUS_APPROVED, invoiced_on__isnull=True, lem_date__lte=filters.date_to,
                           project__billable=True)
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

        # Overhead: non-billable time at wage cost.
        entries = list(filters.entries(use_work=False))
        split = filters.split(entries)
        wages = WageRates(filters.workspace_id)
        total_hours = ZERO
        overhead = {}
        for entry in entries:
            hours = Decimal(entry.duration) / 60
            total_hours += hours
            if entry.is_billable_to_client:
                continue
            straight, overtime = split.get(entry.id, (hours, ZERO))
            rate, ot_rate, _source = wages.for_entry(entry)
            name = entry.project.name if entry.project else "No project"
            row = overhead.setdefault(name, {"name": name, "hours": ZERO, "cost": ZERO, "people": set(), "missing_wage_hours": ZERO})
            row["hours"] += hours
            row["people"].add(entry.user_id)
            if rate is None:
                row["missing_wage_hours"] += hours
            else:
                row["cost"] += straight * rate + overtime * ot_rate
        overhead_rows = sorted(overhead.values(), key=lambda r: r["cost"], reverse=True)
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
                "missing_wage_hours": _hours(sum((r["missing_wage_hours"] for r in overhead_rows), ZERO)),
                "rows": [
                    {"name": r["name"], "hours": _hours(r["hours"]), "cost": _money(r["cost"]), "people": len(r["people"]),
                     "missing_wage_hours": _hours(r["missing_wage_hours"])}
                    for r in overhead_rows
                ],
            },
        })


class ExpertProfitabilityView(_ExpertView):
    """Billed labour at bill rates against wage cost, by job and by employee. Admins only."""

    roles = ADMIN_ONLY

    def build(self, request, filters):
        entries = [e for e in filters.entries(use_work=False) if e.is_billable_to_client]
        split = filters.split(entries)
        wages = WageRates(filters.workspace_id)
        jobs = {}
        people = {}
        missing = defaultdict(lambda: ZERO)

        def bucket(store, key, seed):
            return store.setdefault(key, {**seed, "hours": ZERO, "st": ZERO, "ot": ZERO, "billed": ZERO, "cost": ZERO,
                                          "titles": defaultdict(lambda: ZERO), "sources": defaultdict(lambda: ZERO)})

        for entry in entries:
            hours = Decimal(entry.duration) / 60
            straight, overtime = split.get(entry.id, (hours, ZERO))
            rate, ot_rate, source = wages.for_entry(entry)
            billed = Decimal(entry.cost or 0)
            cost = straight * rate + overtime * ot_rate if rate is not None else ZERO
            if rate is None:
                missing[entry.user.get_full_name() or entry.user.email] += hours
            project = entry.project
            job = bucket(jobs, project.id if project else None, {
                "project_id": str(project.id) if project else None,
                "name": project.name if project else "No project",
                "code": (project.job_code or "") if project else "",
                "client": project.client.name if project and project.client else "",
            })
            person = bucket(people, entry.user_id, {
                "user_id": str(entry.user_id), "name": entry.user.get_full_name() or entry.user.email,
            })
            for row in (job, person):
                row["hours"] += hours
                row["st"] += straight
                row["ot"] += overtime
                row["billed"] += billed
                row["cost"] += cost
            person["titles"][entry.job_title.name if entry.job_title else ""] += hours
            person["sources"][(source, rate)] += hours

        def finish(row, kind):
            margin = row["billed"] - row["cost"]
            out = {
                key: value for key, value in row.items() if key not in ("titles", "sources", "hours", "st", "ot", "billed", "cost")
            }
            out.update({
                "hours": _hours(row["hours"]), "st_hours": _hours(row["st"]), "ot_hours": _hours(row["ot"]),
                "billed": _money(row["billed"]), "wage_cost": _money(row["cost"]), "margin": _money(margin),
                "margin_pct": _money(margin / row["billed"] * 100) if row["billed"] else None,
                "bill_rate": _money(row["billed"] / row["hours"]) if row["hours"] else None,
            })
            if kind == "person":
                out["job_title"] = max(row["titles"].items(), key=lambda kv: kv[1])[0] if row["titles"] else ""
                (source, rate), _h = max(row["sources"].items(), key=lambda kv: kv[1]) if row["sources"] else ((None, None), 0)
                out["wage_rate"] = _money(rate) if rate is not None else None
                out["wage_source"] = source
            return out

        totals = {key: sum((row[key] for row in jobs.values()), ZERO) for key in ("hours", "st", "ot", "billed", "cost")}
        margin = totals["billed"] - totals["cost"]
        return Response({
            "period": {"date_from": filters.date_from, "date_to": filters.date_to},
            "summary": {
                "hours": _hours(totals["hours"]), "st_hours": _hours(totals["st"]), "ot_hours": _hours(totals["ot"]),
                "billed": _money(totals["billed"]), "wage_cost": _money(totals["cost"]), "margin": _money(margin),
                "margin_pct": _money(margin / totals["billed"] * 100) if totals["billed"] else None,
                "bill_rate": _money(totals["billed"] / totals["hours"]) if totals["hours"] else None,
                "missing_wage": [{"name": name, "hours": _hours(h)} for name, h in sorted(missing.items())],
            },
            "by_job": sorted((finish(r, "job") for r in jobs.values()), key=lambda r: r["billed"], reverse=True),
            "by_employee": sorted((finish(r, "person") for r in people.values()), key=lambda r: r["billed"], reverse=True),
        })


def payroll_data(filters):
    """Rows of hours per employee per day for the period, straight time / overtime."""
    entries = list(filters.entries(use_work=False))
    split = filters.split(entries)
    members = {
        m.user_id: m
        for m in WorkspaceMember.objects.filter(workspace_id=filters.workspace_id, is_deleted=False).select_related("crew")
    }
    days = [filters.date_from + timedelta(days=i) for i in range((filters.date_to - filters.date_from).days + 1)]
    rows = {}
    for entry in entries:
        hours = Decimal(entry.duration) / 60
        straight, overtime = split.get(entry.id, (hours, ZERO))
        day = utc_to_envision_local(entry.start_time).date()
        member = members.get(entry.user_id)
        row = rows.setdefault(entry.user_id, {
            "user_id": str(entry.user_id),
            "name": entry.user.get_full_name() or entry.user.email,
            "crew": member.crew.name if member and member.crew_id and member.crew else "",
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
    for row in sorted(rows.values(), key=lambda r: (r["crew"] or "~", r["name"])):
        st = sum((c["st"] for c in row["days"].values()), ZERO)
        ot = sum((c["ot"] for c in row["days"].values()), ZERO)
        out.append({
            "user_id": row["user_id"],
            "name": row["name"],
            "crew": row["crew"],
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
        daily.append(["Employee", "Crew", "Job title", "Date", "Jobs", "Straight time", "Overtime", "Total"])
        for row in data["rows"]:
            for day in row["days"]:
                if day["st"] or day["ot"]:
                    daily.append([row["name"], row["crew"], row["job_title"], day["date"], ", ".join(day["jobs"]),
                                  day["st"], day["ot"], round(day["st"] + day["ot"], 2)])
        totals = book.create_sheet("Totals")
        totals.append(["Employee", "Crew", "Job title", "Straight time", "Overtime", "Total"])
        for row in data["rows"]:
            totals.append([row["name"], row["crew"], row["job_title"], row["st_hours"], row["ot_hours"], row["total_hours"]])
        totals.append(["All employees", "", "", data["totals"]["st_hours"], data["totals"]["ot_hours"], data["totals"]["total_hours"]])
        for sheet in (daily, totals):
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = max(12, min(40, max(len(str(c.value or "")) for c in column) + 2))
        for cell in daily["D"][1:]:
            cell.number_format = "yyyy-mm-dd"

        buffer = io.BytesIO()
        book.save(buffer)
        name = f"Payroll_{filters.date_from:%Y-%m-%d}_to_{filters.date_to:%Y-%m-%d}.xlsx"
        response = HttpResponse(buffer.getvalue(),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
