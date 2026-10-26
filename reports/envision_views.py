"""
Envision GEO — LEM Report API Views
  POST /api/reports/envision/fieldTicket_Lem/           — Field Ticket LEM PDF (whole day)
  POST /api/reports/envision/fieldTicket_Lem/payload/   — Field Ticket LEM PDF (payload entries only)
  POST /api/reports/envision/costing-lem/                — Costing LEM PDF
  GET  /api/reports/envision/lem/search/                 — Search Field Ticket LEM by number
  GET  /api/reports/envision/lem/list/                   — List field tickets (paginated/searchable)
  GET  /api/reports/envision/lem/download/               — Re-download a field ticket's original PDF
  POST /api/reports/envision/lem/void/                   — Void a LEM (soft-delete LEM + its time entries)
  GET  /api/reports/envision/lem/entries/                — A Field Ticket LEM's time entries (to open it for revision)
  POST /api/reports/envision/lem/revise/                 — Revise a Field Ticket LEM (FTF- only, admins): "000012-R01", "-R02"...
  GET  /api/reports/envision/lem/history/                — Every issued revision of a field ticket
"""

import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.http import FileResponse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from core.utils.envision_time import envision_day_bounds_utc, utc_to_envision_local
from projects.models import Project
from tasks.models import Task
from time_entries.field_ticket_views import (
    create_field_ticket_entry,
    sync_approval_total,
    update_field_ticket_entry,
)
from time_entries.models import TimeEntry
from time_entries.serializers import FieldTicketEntrySerializer
from workspaces.models import WorkspaceMember
from workspaces.permissions import ADMIN_ONLY, IsWorkspaceUser, has_workspace_role
from .models import LEMReport, LEMRevision, LEMStatusEvent, format_display_number, split_year_prefix
from user_permissions.models import UserPermission
from .boss_ticket_utils import (
    BOSS_TEMPLATES,
    DEFAULT_TEMPLATE,
    boss_data_from_snapshot,
    build_boss_ticket_data,
    render_boss_ticket,
)
from .envision_costing_pdf_utils import generate_envision_costing_pdf
from .envision_costing_excel_utils import generate_envision_costing_xlsx


# Workspace roles that see, download and void every field ticket in their workspace. Everyone else
# (managers and field managers included) only gets the tickets they created themselves.
FIELD_TICKET_ADMIN_ROLES = {"admin"}


def _address_to_lines(address: str) -> list:
    """Split a comma-separated address string into PDF lines."""
    return [part.strip() for part in (address or "").split(",") if part.strip()]


def _field_ticket_pdf(lem, report_data):
    """(pdf_buffer, filename) for one version of a field ticket (report_data is the current one or a revision
    snapshot), always on a BOSS template: the one stored with it (report_data "boss_template", else
    LEMReport.template, else template 1). Tickets issued before the BOSS templates have no "boss" data; their
    stored snapshot is converted, so they still print what was issued."""
    template = report_data.get("boss_template") or lem.template
    if template not in BOSS_TEMPLATES:
        template = DEFAULT_TEMPLATE
    data = report_data.get("boss")
    if data is None:
        data = boss_data_from_snapshot(report_data, lem.project)
    number = report_data.get("lem_number") or lem.display_number
    day = report_data.get("lem_date") or (str(lem.lem_date) if lem.lem_date else "")
    return render_boss_ticket(template, number, day, data)


def _invalid_template_response(template):
    """400 for a template that isn't a BOSS one; None when it's valid or not given (template 1 is used)."""
    if template and template not in BOSS_TEMPLATES:
        return Response({"error": f"Unknown template '{template}'."}, status=400)
    return None


def _build_field_ticket_labour_and_equipment(entries):
    """Build (labour_rows, equipment_rows, total_cost) for the Field Ticket
    LEM PDF from a list/queryset of TimeEntry — shared by EnvisionLEMReportView
    (queries a whole day) and any endpoint that instead passes in only the
    entries it just created."""
    # ── Build labour rows ─────────────────────────────────────────────────
    labour_map = {}
    for entry in entries:
        jt_name = entry.job_title.name if entry.job_title else "-"
        key     = (entry.user_id, getattr(entry.job_title, "id", None))

        if key not in labour_map:
            labour_map[key] = {
                "name":  entry.user.get_full_name(),
                "role":  jt_name,
                "hours": Decimal("0"),
                "meals": False,
                "hotel": False,
            }

        labour_map[key]["hours"] += Decimal(entry.duration) / 60
        if entry.meals:
            labour_map[key]["meals"] = True
        if entry.hotels:
            labour_map[key]["hotel"] = True

    labour_rows = [
        {
            "name":  row["name"],
            "role":  row["role"],
            "hours": str(round(row["hours"], 2)),
            "meals": "1" if row["meals"] else "0",
            "hotel": "1" if row["hotel"] else "0",
        }
        for row in labour_map.values()
    ]

    # ── Build equipment rows & total cost (equipment only) ───────────────
    asset_map  = {}
    total_cost = Decimal("0")

    for entry in entries:
        for usage in entry.asset_usages.all():
            asset = usage.asset
            # Non-billable time carries no cost.
            cost  = Decimal(usage.cost or 0) if entry.is_billable_to_client else Decimal("0")
            total_cost += cost

            if asset.id not in asset_map:
                asset_map[asset.id] = {
                    "item":  asset.name,
                    "hours": Decimal("0"),
                    "days":  "",
                    "units": Decimal("0"),
                    "rate":  Decimal("0"),
                    "cost":  Decimal("0"),
                }

            asset_map[asset.id]["cost"] += cost

            if asset.charge_type == "hourly":
                # quantity_used is the authoritative value — if it wasn't
                # recorded for this usage, leave it out rather than
                # guessing from the time entry's duration.
                if usage.quantity_used is not None:
                    asset_map[asset.id]["hours"] += Decimal(usage.quantity_used)
                asset_map[asset.id]["rate"] = Decimal(asset.hourly_rate or 0)
            else:
                asset_map[asset.id]["units"] += Decimal(usage.quantity_used or 0)
                asset_map[asset.id]["rate"]   = Decimal(asset.quantity_rate or 0)

    equipment_rows = []
    for row in asset_map.values():
        equipment_rows.append({
            "item":  row["item"],
            "hours": str(round(row["hours"], 2)) if row["hours"] else "",
            "days":  row["days"],
            "units": str(round(row["units"], 2)) if row["units"] else "",
            "rate":  f"${row['rate']:,.2f}",
            "cost":  f"${row['cost']:,.2f}",
        })

    return labour_rows, equipment_rows, total_cost


def _merge_entry_descriptions(entries, override=None):
    """Merge distinct, non-blank TimeEntry descriptions into one paragraph,
    unless `override` (e.g. a user-supplied work_description) is given."""
    if override:
        return override

    seen       = set()
    desc_parts = []
    for e in entries:
        text = (e.description or "").strip()
        if text and text not in seen:
            seen.add(text)
            desc_parts.append(text)
    return ". ".join(desc_parts) + "." if desc_parts else "N/A"


class EnvisionLEMReportView(APIView):
    """
    Generate and download an Envision GEO Field Ticket LEM PDF.

    Address, PM info, and job code are read from the database
    (Workspace.address, Project.pm_info, Project.job_code).

    Required body params:
        project_id       (uuid)  — project to report on
        task_id          (uuid)  — task within the project to report on
        date             (str)   — report date, YYYY-MM-DD

    Optional body params:
        client_rep       (str)   — client representative name
        work_description (str)   — auto-built from time entry descriptions if omitted
        sign             (bool)
        sign_name        (str)
        sign_date        (str)   — YYYY-MM-DD

    Reuses the same FT- number for repeat calls on the same project+task+
    date, refreshing report_data from ALL of that day's entries each time.
    Unaffected by EnvisionFieldTicketLEMFromPayloadView (fieldTicket_Lem/
    payload/), which uses its own separate FTF- number series — the two
    prefixes can never collide (lem_number__startswith distinguishes them).
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        data = request.data

        # ── Validate required fields ──────────────────────────────────────────
        project_id = data.get("project_id")
        task_id    = data.get("task_id")
        date_str   = data.get("date")

        if not project_id or not task_id or not date_str:
            return Response({"error": "project_id, task_id and date are required"}, status=400)

        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        try:
            project = Project.objects.select_related("client", "workspace").get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=404)

        try:
            task = Task.objects.get(id=task_id, project=project)
        except Task.DoesNotExist:
            return Response({"error": "Task not found for this project"}, status=404)

        # ── Pull workspace-level info from DB ─────────────────────────────────
        workspace     = project.workspace
        pm_info       = project.pm_info or {}
        pm_name       = pm_info.get("name", "")
        pm_contact    = pm_info.get("email", "")
        pm_phone      = pm_info.get("phone", "")
        address_lines = _address_to_lines(workspace.address or "")

        # ── Fetch time entries ────────────────────────────────────────────────
        # report_date is a Mountain-Time (Envision GEO) calendar day — match it
        # against the UTC-stored start_time using explicit MT day boundaries,
        # not start_time__date (which depends on server TIME_ZONE, i.e. UTC).
        day_start, day_end = envision_day_bounds_utc(report_date)
        entries = (
            TimeEntry.objects
            .filter(project_id=project_id, task_id=task_id, start_time__gte=day_start, start_time__lte=day_end)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
            .order_by("start_time")
        )

        if not entries.exists():
            return Response(
                {
                    "error": "No time entries found for this project on the selected date.",
                    "project_id": str(project_id),
                    "date": date_str,
                },
                status=404,
            )

        labour_rows, equipment_rows, total_cost = _build_field_ticket_labour_and_equipment(entries)
        work_description = _merge_entry_descriptions(entries, data.get("work_description"))

        # ── Signature fields ──────────────────────────────────────────────────
        user      = request.user
        sign      = data.get("sign", False)
        sign_name = data.get("sign_name") or user.get_full_name()
        sign_date = data.get("sign_date") or date_str

        # ── Reuse the FT LEM number for this project+task+date, or assign a new one ──
        FT_DB_PREFIX = "FT-"
        lem_report = (
            LEMReport.objects
            .filter(
                project=project,
                task=task,
                lem_date=report_date,
                lem_number__startswith=FT_DB_PREFIX,
            )
            .first()
        )
        is_new = lem_report is None
        if is_new:
            # all_objects — a voided LEM's number must never be reused (unique_together).
            last_ft = (
                LEMReport.all_objects
                .filter(project__workspace=workspace, lem_number__startswith=FT_DB_PREFIX)
                .order_by("-id")
                .first()
            )
            try:
                last_num = int(last_ft.lem_number.replace(FT_DB_PREFIX, "")) if last_ft else 0
            except ValueError:
                last_num = 0

            lem_report = LEMReport(
                requester=request.user,
                project=project,
                task=task,
                lem_date=report_date,
                lem_number=f"{FT_DB_PREFIX}{last_num + 1:06d}",
                report_data={},
            )
            lem_report.save()

        ft_display_number = lem_report.display_number

        # ── Build PDF data fresh from current time entries every time ────────
        pdf_data = {
            "lem_number":       ft_display_number,
            "lem_date":         date_str,
            "company_address":  address_lines,
            "project_name":     project.name,
            "task_name":        task.name,
            "job_number":       project.job_code or "",
            "client":           project.client.name if project.client else "",
            "pm_name":          pm_name,
            "pm_contact":       pm_contact,
            "pm_phone":         pm_phone,
            "labour_rows":      labour_rows,
            "work_description": work_description,
            "equipment_rows":   equipment_rows,
            "total_cost":       f"${total_cost:,.2f}",
            "client_rep":       data.get("client_rep", ""),
            "sign":             sign,
            "sign_name":        sign_name if sign else "",
            "sign_date":        sign_date if sign else "",
        }

        pdf_data["boss_template"] = lem_report.template or DEFAULT_TEMPLATE
        pdf_data["boss"] = build_boss_ticket_data(entries, project, task, prepared_by=request.user)
        lem_report.report_data = pdf_data
        lem_report.template = pdf_data["boss_template"]
        lem_report.save()
        # Record exactly which entries this snapshot was built from — voiding
        # this LEM later only touches these, not every entry that happens to
        # match the same project/task/date.
        lem_report.time_entries.set(entries)

        try:
            pdf_buffer, filename = _field_ticket_pdf(lem_report, pdf_data)
        except Exception as exc:
            if is_new:
                lem_report.delete()
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class EnvisionFieldTicketLEMFromPayloadView(APIView):
    """
    POST /api/reports/envision/fieldTicket_Lem/payload/

    Same request body as POST /api/time_entries/field-ticket-entry/
    (FieldTicketBulkEntryView) — an array of per-employee entries, each
    carrying its own project/job_title/task/date/start_time/end_time/
    description/billable/meals/hotels/assets. No other top-level fields.

    Creates those TimeEntry rows exactly like FieldTicketBulkEntryView does,
    then immediately generates a Field Ticket LEM PDF — but unlike
    EnvisionLEMReportView (fieldTicket_Lem/, which re-queries and reports on
    the WHOLE day for the given project/task/date), this LEM's data is built
    ONLY from the entries created in THIS call. Always assigns a brand-new
    number from its OWN "FTF-" (Field Ticket Field) series — a completely
    separate numbering sequence from fieldTicket_Lem/'s "FT-" numbers, so
    the two can never collide or overwrite each other for the same
    project/task/date, and creating/voiding FTF- LEMs never affects FT-
    numbering (or vice versa). Multiple submissions for the same day each
    get their own distinct FTF- field ticket.

    All rows must share the same project, task, and date — a single Field
    Ticket LEM PDF represents exactly one project/task/day. If any entry
    fails to validate/create, the whole batch is rolled back (no partial
    field ticket) and a 400 is returned.

    Because the payload is unchanged from FieldTicketBulkEntryView's bare
    array, there's no room for client_rep/work_description/signature fields
    in this request — they're left blank/false. work_description is always
    auto-built from the created entries' descriptions.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    FTF_DB_PREFIX = "FTF-"

    def post(self, request):
        # ?template=daily_ticket|cost_sheet issues the ticket on that BOSS template (the body is a bare array);
        # template 1 when none is given.
        template = request.query_params.get("template") or DEFAULT_TEMPLATE
        invalid = _invalid_template_response(template)
        if invalid:
            return invalid

        serializer = FieldTicketEntrySerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        validated = list(serializer.validated_data)

        if not validated:
            return Response({"error": "At least one entry is required."}, status=400)

        # All rows must share the same project + task + date — one Field
        # Ticket LEM PDF represents exactly one project/task/day.
        first_row  = validated[0]
        project_id = first_row["project"]
        task_id    = first_row.get("task")
        entry_date = first_row["date"]

        if not task_id:
            return Response({"error": "Each entry must include a task for a Field Ticket LEM."}, status=400)

        for row in validated[1:]:
            if row["project"] != project_id or row.get("task") != task_id or row["date"] != entry_date:
                return Response(
                    {"error": "All entries must share the same project, task, and date."},
                    status=400,
                )

        try:
            project = Project.objects.select_related("client", "workspace").get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=404)

        try:
            task = Task.objects.get(id=task_id, project=project)
        except Task.DoesNotExist:
            return Response({"error": "Task not found for this project"}, status=404)

        # All-or-nothing: if any row fails, roll back the whole batch rather
        # than generate a field ticket that's silently missing an employee.
        created_entry_ids = []
        try:
            with transaction.atomic():
                for index, row in enumerate(validated):
                    try:
                        entry = create_field_ticket_entry(request, row)
                    except Exception as exc:
                        raise ValueError(f"Entry {index}: {exc}") from exc
                    created_entry_ids.append(entry.id)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)

        entries = (
            TimeEntry.objects
            .filter(id__in=created_entry_ids)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
            .order_by("start_time")
        )

        workspace     = project.workspace
        pm_info       = project.pm_info or {}
        pm_name       = pm_info.get("name", "")
        pm_contact    = pm_info.get("email", "")
        pm_phone      = pm_info.get("phone", "")
        address_lines = _address_to_lines(workspace.address or "")

        labour_rows, equipment_rows, total_cost = _build_field_ticket_labour_and_equipment(entries)
        work_description = _merge_entry_descriptions(entries)

        date_str = entry_date.strftime("%Y-%m-%d")

        # Always a brand-new LEM in this endpoint's OWN "FTF-" number series —
        # a separate sequence from fieldTicket_Lem/'s "FT-" numbers (the two
        # prefixes can never match each other's lem_number__startswith).
        last_ftf = (
            LEMReport.all_objects
            .filter(project__workspace=workspace, lem_number__startswith=self.FTF_DB_PREFIX)
            .order_by("-id")
            .first()
        )
        try:
            last_num = int(last_ftf.lem_number.replace(self.FTF_DB_PREFIX, "")) if last_ftf else 0
        except ValueError:
            last_num = 0

        lem_report = LEMReport(
            requester=request.user,
            project=project,
            task=task,
            lem_date=entry_date,
            lem_number=f"{self.FTF_DB_PREFIX}{last_num + 1:06d}",
            report_data={},
        )
        lem_report.save()

        ft_display_number = lem_report.lem_number.replace(self.FTF_DB_PREFIX, "")

        pdf_data = {
            "lem_number":       ft_display_number,
            "lem_date":         date_str,
            "company_address":  address_lines,
            "project_name":     project.name,
            "task_name":        task.name,
            "job_number":       project.job_code or "",
            "client":           project.client.name if project.client else "",
            "pm_name":          pm_name,
            "pm_contact":       pm_contact,
            "pm_phone":         pm_phone,
            "labour_rows":      labour_rows,
            "work_description": work_description,
            "equipment_rows":   equipment_rows,
            "total_cost":       f"${total_cost:,.2f}",
            "client_rep":       "",
            "sign":             False,
            "sign_name":        "",
            "sign_date":        "",
        }
        pdf_data["boss_template"] = template
        pdf_data["boss"] = build_boss_ticket_data(entries, project, task, prepared_by=request.user)

        lem_report.report_data = pdf_data
        lem_report.template = template
        lem_report.save()
        # Record exactly these entries — this is the whole point of this
        # endpoint, so voiding it later only ever touches this submission.
        lem_report.time_entries.set(entries)
        # Submitted: pending the consultant's / contractor's approval.
        lem_report.set_status(LEMReport.STATUS_PENDING, request.user, "Submitted")

        try:
            pdf_buffer, filename = _field_ticket_pdf(lem_report, pdf_data)
        except Exception as exc:
            lem_report.delete()
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class EnvisionFieldTicketListView(APIView):
    """
    GET /api/reports/envision/lem/list/

    Paginated, searchable list of tickets, one series at a time — the
    frontend's three tabs ("Field Ticket - Complete Day", "Field Ticket -
    Field", "Costing Ticket") each pass a different `type`:
      - type=FT   -> FT-  (whole-day field ticket)
      - type=FTF  -> FTF- (per-submission field ticket)
      - type=CT   -> CT-  (costing ticket)
    Defaults to FT- if `type` is missing/unrecognised.

    Visibility:
      - Superuser: every ticket of that type, optionally narrowed to one
        workspace via ?workspace=<id>.
      - Workspace admin, or anyone with the "Approve / reject field tickets"
        permission there: every ticket of that type in that workspace, PLUS
        their own (as requester) in any other workspace they belong to.
      - Everyone else (managers and field managers included): only tickets
        of that type where they are the requester.

    Query params (all optional except `type`):
        type        (str)   — "FT" | "FTF" | "CT", default "FT"
        search      (str)   — matches lem_number or project name, contains
        date_from   (str)   — YYYY-MM-DD, filters on the LEM date (created date if a ticket has none)
        date_to     (str)   — YYYY-MM-DD, filters on the LEM date (created date if a ticket has none)
        ordering    (str)   — "lem_date" | "-lem_date" | "created_at" | "-created_at" | "lem_number" | "-lem_number"
        page        (int)   — default 1
        page_size   (int)   — default 20, max 100
        workspace   (uuid)  — superuser only, narrows to one workspace

    Each row's total_hours/asset_info are read from the ticket's stored
    report_data snapshot (frozen at generation time), not recomputed live —
    matching what EnvisionFieldTicketDownloadView will actually produce.
    FT-/FTF- tickets store report_data as labour_rows/equipment_rows; CT-
    tickets store it as labour_groups grouped by asset (job titles play no
    part in Costing Tickets — see EnvisionCostingLEMView) with no separate
    asset section, so total_hours/asset_info are derived differently for CT.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    ELEVATED_ROLES = FIELD_TICKET_ADMIN_ROLES
    TYPE_PREFIXES = {"FT": "FT-", "FTF": "FTF-", "CT": "CT-"}
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100
    ALLOWED_ORDERING = {"lem_date", "-lem_date", "created_at", "-created_at", "lem_number", "-lem_number"}

    def get(self, request):
        user = request.user
        ticket_type = request.GET.get("type", "FT").upper()
        prefix = self.TYPE_PREFIXES.get(ticket_type, self.TYPE_PREFIXES["FT"])

        qs = (
            LEMReport.objects
            .filter(lem_number__startswith=prefix, project__isnull=False)
            .select_related("project", "project__workspace", "requester")
            .annotate(effective_date=Coalesce("lem_date", TruncDate("created_at")))
        )

        # Workspaces where this user may revise and delete tickets: the ones they admin (None = all, for superusers).
        self._revise_workspace_ids = None if user.is_superuser else set(
            WorkspaceMember.objects.filter(user=user, role="admin", is_active=True).values_list("workspace_id", flat=True)
        )
        # ... and where they may mark tickets approved / rejected.
        self._status_workspace_ids = _lem_status_workspace_ids(user)

        status = request.GET.get("status", "").strip().lower()
        if status:
            if status not in dict(LEMReport.STATUS_CHOICES):
                return Response({"error": "status must be pending, approved or rejected"}, status=400)
            qs = qs.filter(status=status)

        if user.is_superuser:
            workspace_id = request.GET.get("workspace")
            if workspace_id:
                qs = qs.filter(project__workspace_id=workspace_id)
        else:
            elevated_workspace_ids = _lem_viewer_workspace_ids(user)
            if elevated_workspace_ids:
                qs = qs.filter(Q(project__workspace_id__in=elevated_workspace_ids) | Q(requester=user))
            else:
                qs = qs.filter(requester=user)

        search, search_year = split_year_prefix(request.GET.get("search", ""))
        if search_year is not None:
            # "26-000123": that number on a ticket dated in 2026
            qs = qs.filter(effective_date__year=2000 + search_year)
        if search:
            revision_match = re.match(r"^(.+?)-r(\d+)$", search, re.IGNORECASE)
            if revision_match:
                # "000012-R01": the number plus that revision
                qs = qs.filter(lem_number__icontains=revision_match.group(1), revision=int(revision_match.group(2)))
            else:
                qs = qs.filter(Q(lem_number__icontains=search) | Q(project__name__icontains=search))

        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")
        if date_from:
            qs = qs.filter(effective_date__gte=date_from)
        if date_to:
            qs = qs.filter(effective_date__lte=date_to)

        ordering = request.GET.get("ordering", "-lem_date")
        if ordering not in self.ALLOWED_ORDERING:
            ordering = "-lem_date"
        # the LEM date, falling back to the created date for any ticket without one
        order_field = ordering.replace("lem_date", "effective_date")
        qs = qs.order_by(order_field, "-id")

        try:
            page = max(int(request.GET.get("page", 1)), 1)
            page_size = min(max(int(request.GET.get("page_size", self.DEFAULT_PAGE_SIZE)), 1), self.MAX_PAGE_SIZE)
        except ValueError:
            return Response({"error": "page and page_size must be integers"}, status=400)

        count = qs.count()
        start = (page - 1) * page_size
        page_qs = qs[start:start + page_size]

        serialize = self._serialize_costing if prefix == "CT-" else self._serialize_field_ticket
        return Response({"results": [serialize(lem) for lem in page_qs], "count": count})

    def _base_fields(self, lem):
        return {
            "id": lem.id,
            "lem_number": lem.lem_number,
            "revision": lem.revision,
            "display_number": lem.ticket_number,
            "can_revise": lem.lem_number.startswith(REVISABLE_PREFIX) and (
                self._revise_workspace_ids is None or lem.project.workspace_id in self._revise_workspace_ids
            ),
            "can_delete": (
                self._revise_workspace_ids is None or lem.project.workspace_id in self._revise_workspace_ids
            ),
            "template": lem.template or DEFAULT_TEMPLATE,
            "template_label": dict(LEMReport.TEMPLATE_CHOICES)[lem.template or DEFAULT_TEMPLATE],
            **_lem_status_fields(lem),
            "can_change_status": bool(lem.project_id) and (
                self._status_workspace_ids is None or lem.project.workspace_id in self._status_workspace_ids
            ),
            "project_name": lem.project.name if lem.project else "-",
            "lem_date": str(lem.lem_date) if lem.lem_date else timezone.localtime(lem.created_at).date().isoformat(),
            "created_at": lem.created_at,
            "created_by": lem.requester.get_full_name() if lem.requester else "-",
        }

    def _serialize_field_ticket(self, lem):
        """FT-/FTF- shape: report_data.labour_rows / .equipment_rows (see
        envision_pdf_utils.py's docstring)."""
        data = lem.report_data or {}

        total_hours = Decimal("0")
        for row in data.get("labour_rows", []):
            try:
                total_hours += Decimal(str(row.get("hours") or 0))
            except (InvalidOperation, TypeError):
                pass

        asset_info = [
            {
                "name": row.get("item", ""),
                "hours": row.get("hours") or "",
                "units": row.get("units") or "",
                "cost": row.get("cost", ""),
            }
            for row in data.get("equipment_rows", [])
        ]

        return {
            **self._base_fields(lem),
            "total_hours": _fmt_money(total_hours),
            "asset_info": asset_info,
        }

    def _serialize_costing(self, lem):
        """CT- shape: report_data.labour_groups, each group keyed by the
        linked asset's name (job titles play no part — see
        _EnvisionCostingLEMDataMixin), with no separate asset section."""
        data = lem.report_data or {}
        groups = data.get("labour_groups", [])

        total_hours = Decimal("0")
        for group in groups:
            try:
                total_hours += Decimal(str(group.get("hours_total") or 0))
            except (InvalidOperation, TypeError):
                pass

        asset_info = [
            {
                "name": group.get("job_title", ""),
                "hours": group.get("hours_total") or "",
                "units": "",
                "cost": "${}".format(_fmt_money(group.get("subtotal") or 0)),
            }
            for group in groups
        ]

        return {
            **self._base_fields(lem),
            "total_hours": _fmt_money(total_hours),
            "asset_info": asset_info,
        }


class EnvisionFieldTicketDownloadView(APIView):
    """
    GET /api/reports/envision/lem/download/?lem_number=FT-000012

    Re-renders the PDF for an existing ticket (FT-, FTF-, or CT-) from its
    stored report_data snapshot — the exact content as originally issued,
    never recomputed from current live time entries/assets. CT- (Costing)
    tickets use the Costing PDF generator (different report_data shape —
    see EnvisionFieldTicketListView); FT-/FTF- use the field ticket one.
    Same visibility rule as EnvisionFieldTicketListView.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    ELEVATED_ROLES = FIELD_TICKET_ADMIN_ROLES

    def get(self, request):
        raw = request.GET.get("lem_number", "").strip()
        if not raw:
            return Response({"error": "lem_number query parameter is required"}, status=400)

        lem_report = (
            _lems_visible_to(request.user)
            .select_related("project__workspace", "requester")
            .filter(lem_number=raw)
            .order_by("-id")
            .first()
        )
        if not lem_report:
            return Response({"error": f"No field ticket found for number '{raw}'"}, status=404)

        if not _can_view_lem(request.user, lem_report):
            return Response({"error": "You do not have permission to download this field ticket."}, status=403)

        # ?revision=N downloads an earlier issued version; the current one needs no parameter.
        report_data = lem_report.report_data
        revision = lem_report.revision
        requested_revision = request.GET.get("revision", "").strip()
        if requested_revision:
            try:
                requested_revision = int(requested_revision)
            except ValueError:
                return Response({"error": "revision must be a number"}, status=400)
            if requested_revision != lem_report.revision:
                snapshot = lem_report.revisions.filter(revision=requested_revision).first()
                if not snapshot:
                    return Response({"error": f"Revision {requested_revision} not found."}, status=404)
                report_data = snapshot.report_data
                revision = requested_revision

        is_costing = lem_report.lem_number.startswith("CT-")
        digits = lem_report.ticket_number_for(revision)
        date_part = str(lem_report.lem_date) if lem_report.lem_date else ""
        prefix_label = "Costing_LEM" if is_costing else "Field_Ticket_LEM"
        filename = f"{prefix_label}_{digits}_{date_part}.pdf"
        try:
            if is_costing:
                pdf_buffer = generate_envision_costing_pdf(report_data)
            else:
                # Same BOSS template the ticket was issued on.
                pdf_buffer, filename = _field_ticket_pdf(lem_report, report_data)
        except Exception as exc:
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class EnvisionLEMSearchView(APIView):
    """
    Look up a saved Envision Field Ticket LEM by number.
    Searches the FTF- (Field Ticket Field, from EnvisionFieldTicketLEMFromPayloadView)
    series only — not the FT- whole-day series from EnvisionLEMReportView.
    Accepts the display number (digits only) or the full FTF- prefixed form.

    GET /api/reports/envision/lem/search/?lem_number=000001
    GET /api/reports/envision/lem/search/?lem_number=FTF-000001
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    FTF_PREFIX = "FTF-"

    def get(self, request):
        raw = request.query_params.get("lem_number", "").strip()
        if not raw:
            return Response({"error": "lem_number query parameter is required"}, status=400)

        # Normalise: strip a "26-" year and the FTF- prefix if the caller included them, then re-add FTF-
        digits = split_year_prefix(raw)[0].replace(self.FTF_PREFIX, "").strip()
        db_key = f"{self.FTF_PREFIX}{digits}"

        report = _lems_visible_to(request.user).select_related("project__workspace").filter(lem_number=db_key).first()
        if not report or not _can_view_lem(request.user, report):
            return Response(
                {"error": f"No Field Ticket LEM found for number '{digits}'"},
                status=404,
            )

        return Response({
            "lem_number":  digits,
            "revision":    report.revision,
            "display_number": report.ticket_number,
            "report_data": report.report_data,
            "created_at":  report.created_at,
            "project":     str(report.project_id) if report.project else None,
            "task":        str(report.task_id) if report.task else None,
            "lem_date":    str(report.lem_date) if report.lem_date else None,
            "requester":   report.requester.get_full_name() if report.requester else None,
        })


class EnvisionLEMVoidView(APIView):
    """
    POST /api/reports/envision/lem/void/

    Void a LEM: soft-deletes the LEMReport row itself, plus exactly the
    TimeEntry rows recorded on its `time_entries` relation — i.e. only the
    entries that were actually part of THIS LEM's snapshot the last time it
    was (re)generated, never other entries that just happen to share the
    same project/task/date (entered before, after, or as part of a
    different LEM). Also soft-deletes each of those entries' AssetUsage
    rows and TimeEntryApprovalItem rows, and any TimeEntryApproval left with
    no items after that. None of these show up in reports once their
    TimeEntry is gone. This makes the LEM number stop showing up in
    EnvisionLEMSearchView and frees its entries to be re-entered under a
    new LEM. Records who voided it and when (deleted_by / deleted_at).

    Works on both the FT- (whole-day, from EnvisionLEMReportView) and FTF-
    (per-submission, from EnvisionFieldTicketLEMFromPayloadView) field
    ticket series. CT- (Costing) and generic LEM- numbers cannot be voided
    through this endpoint.

    Required body params:
        lem_number  (str)  — fully prefixed (e.g. "FT-000012"), or bare
                             digits, which default to the FTF- series for
                             backwards compatibility with the existing
                             manual "void by number" search.

    Allowed for: an admin (or superuser) within the workspace that owns the
    LEM's project. The person who submitted it can't delete it themselves.

    Note: LEMs generated before the `time_entries` relation existed have
    nothing recorded on it, so voiding one soft-deletes the LEMReport but
    no time entries — there's no reliable way to know which ones were
    "this" LEM's after the fact.
    """

    permission_classes = [IsAuthenticated]
    ELEVATED_ROLES = FIELD_TICKET_ADMIN_ROLES

    def post(self, request):
        raw = split_year_prefix(str(request.data.get("lem_number", "")))[0]
        if not raw:
            return Response({"error": "lem_number is required"}, status=400)

        lem_report = self._find_lem(raw, request.user)
        if not lem_report:
            return Response({"error": f"No LEM found for number '{raw}'"}, status=404)

        if not lem_report.project_id:
            return Response(
                {"error": "This LEM's project no longer exists — cannot identify its time entries."},
                status=400,
            )

        workspace = lem_report.project.workspace
        user = request.user
        if not user.is_superuser:
            is_elevated = WorkspaceMember.objects.filter(
                user=user, workspace=workspace, role__in=self.ELEVATED_ROLES
            ).exists()
            if not is_elevated:
                return Response(
                    {"error": "You do not have permission to void LEMs in this workspace."},
                    status=403,
                )

        # Exactly the entries recorded against this LEM — not a re-query by
        # project/task/date, which would also catch entries from other LEMs
        # or entered before/after this one on the same day.
        entries = list(lem_report.time_entries.filter(is_deleted=False))

        # Approvals that reference any of these entries — synced (or voided
        # themselves, if left empty) after the entries are gone, so their
        # total_hours never counts a soft-deleted entry.
        approvals = list(
            TimeEntryApproval.objects.filter(items__time_entry__in=entries).distinct()
        )

        with transaction.atomic():
            for entry in entries:
                for asset_usage in entry.asset_usages.all():
                    asset_usage.delete()
                for approval_item in TimeEntryApprovalItem.objects.filter(time_entry=entry):
                    approval_item.delete()
                entry.delete()  # SoftDeleteModel.delete() -> is_deleted=True

            for approval in approvals:
                self._sync_approval_after_delete(approval)

            lem_report.deleted_by = user
            lem_report.delete()

        return Response({
            "message": f"LEM {lem_report.lem_number} voided.",
            "lem_number": lem_report.lem_number,
            "time_entries_voided": len(entries),
        })

    def _sync_approval_after_delete(self, approval):
        """Same pattern as TimeEntryViewSet._sync_approval_after_delete —
        drop the approval if it's left with no items, otherwise recompute
        total_hours from the entries that are still active."""
        active_items = TimeEntryApprovalItem.objects.filter(approval=approval)

        if not active_items.exists():
            approval.delete()
            return

        total_minutes = (
            active_items.filter(time_entry__is_deleted=False)
            .aggregate(total_minutes=Sum("time_entry__duration"))
            .get("total_minutes")
            or 0
        )
        approval.total_hours = Decimal(total_minutes) / Decimal("60")
        approval.save(update_fields=["total_hours"])

    FTF_PREFIX = "FTF-"
    FT_PREFIX = "FT-"

    def _find_lem(self, raw, user):
        """Matches both field ticket series (FT- whole-day, FTF- per-
        submission). A fully-prefixed number is looked up exactly as given.
        Bare digits (no recognizable prefix at all) default to the FTF-
        series, for backwards compatibility with the existing manual "void
        by number" search box. A fully-prefixed CT-/LEM- number is REJECTED
        outright (returns None -> 404) — it must never be silently stripped
        and re-looked-up as FTF-, which would void a same-numbered but
        completely unrelated ticket."""
        raw = raw.strip()
        lems = _lems_visible_to(user)
        if raw.startswith(self.FTF_PREFIX) or raw.startswith(self.FT_PREFIX):
            return lems.filter(lem_number=raw).first()
        if raw.startswith("CT-") or raw.startswith("LEM-"):
            return None

        db_key = f"{self.FTF_PREFIX}{raw}"
        return lems.filter(lem_number=db_key).first()


# ─────────────────────────────────────────────────────────────────────────────
#  Field ticket revisions
# ─────────────────────────────────────────────────────────────────────────────

def _lems_visible_to(user):
    """LEM numbers run per workspace (every workspace has its own FTF-000001), so a lookup by number only
    searches the workspaces `user` belongs to. Superusers search all of them."""
    lems = LEMReport.objects.all()
    if user.is_superuser:
        return lems
    workspace_ids = WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
    return lems.filter(project__workspace_id__in=workspace_ids)


def _find_field_ticket_lem(raw, user):
    """FT-/FTF- numbers exactly as given; bare digits default to the FTF- series (same rule as the void
    endpoint); CT-/LEM- numbers are never revisable and never match. Only `user`'s workspaces are searched."""
    raw = split_year_prefix(raw)[0]
    lems = _lems_visible_to(user).select_related("project__workspace", "requester")
    if raw.startswith("FTF-") or raw.startswith("FT-"):
        return lems.filter(lem_number=raw).first()
    if raw.startswith("CT-") or raw.startswith("LEM-"):
        return None
    return lems.filter(lem_number=f"FTF-{raw}").first()


def _lem_viewer_workspace_ids(user):
    """Workspaces where `user` sees every field ticket, not just their own: those they admin, and those where
    they have the "Approve / reject field tickets" permission (they need the tickets to decide on them)."""
    admin_ids = WorkspaceMember.objects.filter(
        user=user, role__in=FIELD_TICKET_ADMIN_ROLES,
    ).values_list("workspace_id", flat=True)
    granted_ids = UserPermission.objects.filter(user=user, lem_approval=True).values_list("workspace_id", flat=True)
    return set(admin_ids) | set(granted_ids)


def _can_view_lem(user, lem):
    if user.is_superuser or lem.requester_id == user.id:
        return True
    return bool(lem.project_id) and lem.project.workspace_id in _lem_viewer_workspace_ids(user)


def _lem_status_workspace_ids(user):
    """Workspaces where `user` may mark field tickets approved / rejected (None = all, for superusers):
    workspace admins, and anyone given the "Approve / reject field tickets" permission there."""
    if user.is_superuser:
        return None
    admin_ids = WorkspaceMember.objects.filter(user=user, role="admin", is_active=True).values_list("workspace_id", flat=True)
    granted_ids = UserPermission.objects.filter(user=user, lem_approval=True).values_list("workspace_id", flat=True)
    return set(admin_ids) | set(granted_ids)


def _can_change_lem_status(user, lem):
    if not lem.project_id:
        return False
    allowed = _lem_status_workspace_ids(user)
    return allowed is None or lem.project.workspace_id in allowed


def _lem_status_fields(lem):
    by = lem.status_changed_by
    return {
        "status": lem.status,
        "status_label": lem.get_status_display(),
        "status_note": lem.status_note,
        "status_changed_by": (by.get_full_name() or by.email) if by else "",
        "status_changed_at": lem.status_changed_at,
    }


class EnvisionLEMStatusView(APIView):
    """
    POST /api/reports/envision/lem/status/   {"lem_number": "...", "status": "approved" | "rejected", "note": "..."}

    Records the consultant's / contractor's decision on a submitted field ticket. Allowed for workspace admins,
    superusers and users with the "Approve / reject field tickets" permission in the ticket's workspace. A
    rejection needs a reason; a rejected ticket is revised (lem/revise/), which puts it back to pending.
    """

    permission_classes = [IsAuthenticated]
    SETTABLE = {LEMReport.STATUS_APPROVED, LEMReport.STATUS_REJECTED, LEMReport.STATUS_PENDING}

    def post(self, request):
        lem = _find_field_ticket_lem(str(request.data.get("lem_number", "")), request.user)
        if not lem:
            return Response({"error": "Field ticket not found."}, status=404)
        if not _can_change_lem_status(request.user, lem):
            return Response({"error": "You do not have permission to approve or reject field tickets."}, status=403)

        status = str(request.data.get("status", "")).strip().lower()
        if status not in self.SETTABLE:
            return Response({"error": "status must be approved, rejected or pending"}, status=400)
        note = str(request.data.get("note") or "").strip()
        if status == LEMReport.STATUS_REJECTED and not note:
            return Response({"error": "Give a reason for the rejection."}, status=400)

        lem.set_status(status, request.user, note)
        return Response({"lem_number": lem.lem_number, "display_number": lem.ticket_number, **_lem_status_fields(lem)})


REVISABLE_PREFIX = "FTF-"
NOT_REVISABLE_MESSAGE = "Only Field Ticket LEMs can be revised (not Daily LEM or Costing tickets)."


def _can_revise_lem(user, lem):
    """Workspace admins of the ticket's workspace and superusers; Field Ticket LEMs (FTF-) only — never the
    Daily LEM (FT-), Costing (CT-) or generic (LEM-) series."""
    if not lem.lem_number.startswith(REVISABLE_PREFIX) or not lem.project_id:
        return False
    return has_workspace_role(user, lem.project.workspace_id, ADMIN_ONLY)


def _sum_labour_hours(report_data):
    total = Decimal("0")
    for row in (report_data or {}).get("labour_rows", []):
        try:
            total += Decimal(str(row.get("hours") or 0))
        except (InvalidOperation, TypeError):
            pass
    return total


class FieldTicketRevisionEntrySerializer(FieldTicketEntrySerializer):
    """A payload row of the revise endpoint: `id` set = edit that existing time entry, absent = a new one."""
    id = serializers.UUIDField(required=False)


class EnvisionFieldTicketEntriesView(APIView):
    """
    GET /api/reports/envision/lem/entries/?lem_number=FT-000012

    The ticket's current time entries in the shape the Field Ticket form works with, so an admin can open the
    ticket for revision. Same people as the revise endpoint: workspace admins and superusers.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        lem = _find_field_ticket_lem(request.GET.get("lem_number"), request.user)
        if not lem:
            return Response({"error": "Field ticket not found."}, status=404)
        if not lem.lem_number.startswith(REVISABLE_PREFIX):
            return Response({"error": NOT_REVISABLE_MESSAGE}, status=400)
        if not _can_revise_lem(request.user, lem):
            return Response({"error": "Only workspace admins can revise field tickets."}, status=403)

        entries = (
            lem.time_entries.filter(is_deleted=False)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
            .order_by("start_time", "created_at")
        )
        rows = []
        for e in entries:
            start_local = utc_to_envision_local(e.start_time)
            end_local = utc_to_envision_local(e.end_time)
            rows.append({
                "id": str(e.id),
                "user": str(e.user_id),
                "user_name": e.user.get_full_name() or e.user.email,
                "job_title": str(e.job_title_id) if e.job_title_id else None,
                "description": e.description or "",
                "date": start_local.date().isoformat(),
                "end_date": end_local.date().isoformat(),
                "start_time": start_local.strftime("%H:%M"),
                "end_time": end_local.strftime("%H:%M"),
                "duration_hours": str(round(Decimal(e.duration or 0) / 60, 4).normalize()),
                "billable": bool(e.billable),
                "meals": bool(e.meals),
                "hotels": bool(e.hotels),
                "assets": [
                    {
                        "asset_id": str(u.asset_id),
                        "name": u.asset.name,
                        "charge_type": u.asset.charge_type,
                        "quantity_used": str(u.quantity_used) if u.quantity_used is not None else None,
                    }
                    for u in e.asset_usages.all()
                ],
            })

        return Response({
            "lem_number": lem.lem_number,
            "display_number": lem.ticket_number,
            "revision": lem.revision,
            "project": str(lem.project_id),
            "project_name": lem.project.name,
            "task": str(lem.task_id) if lem.task_id else None,
            "task_name": lem.task.name if lem.task_id else "",
            "date": str(lem.lem_date) if lem.lem_date else (rows[0]["date"] if rows else None),
            "template": lem.template or (lem.report_data or {}).get("boss_template") or DEFAULT_TEMPLATE,
            "entries": rows,
        })


class EnvisionFieldTicketReviseView(APIView):
    """
    POST /api/reports/envision/lem/revise/

    Body: {"lem_number": "FT-000012", "entries": [ <row>, ... ]}
    Each row has the same fields as the create endpoint (fieldTicket_Lem/payload/) plus an optional `id`:
      - `id` of one of the ticket's current time entries  -> that entry is updated in place
      - no `id`                                           -> a new time entry is added (e.g. another employee)
      - a current entry that is not in the list           -> removed from the timesheet and the approvals
    The ticket's project, task and date can't be changed (void and re-enter for that).

    Everything happens in one transaction: time entries, asset usages, weekly approvals (item moved if the
    employee/week changed, total_hours recalculated — admins may edit approved/locked entries), the LEM's
    report_data/PDF, and a new LEMRevision snapshot. The LEM number itself never changes; the revision shows
    as a suffix — "000012-R01", "000012-R02"... Responds with the new PDF.

    Workspace admins (of the ticket's workspace) and superusers only.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        lem = _find_field_ticket_lem(str(request.data.get("lem_number", "")), request.user)
        if not lem:
            return Response({"error": "Field ticket not found."}, status=404)
        if not lem.lem_number.startswith(REVISABLE_PREFIX):
            return Response({"error": NOT_REVISABLE_MESSAGE}, status=400)
        if not _can_revise_lem(request.user, lem):
            return Response({"error": "Only workspace admins can revise field tickets."}, status=403)
        invalid = _invalid_template_response(request.data.get("template"))
        if invalid:
            return invalid

        serializer = FieldTicketRevisionEntrySerializer(data=request.data.get("entries"), many=True)
        serializer.is_valid(raise_exception=True)
        rows = list(serializer.validated_data)
        if not rows:
            return Response(
                {"error": "At least one entry is required. To remove the whole ticket, delete it instead."},
                status=400,
            )

        for row in rows:
            if row["project"] != lem.project_id or row.get("task") != lem.task_id:
                return Response({"error": "A revision can't change the ticket's project or task."}, status=400)
            if lem.lem_date and row["date"] != lem.lem_date:
                return Response({"error": "A revision can't change the ticket's date."}, status=400)
        payload_ids = [row["id"] for row in rows if row.get("id")]
        if len(payload_ids) != len(set(payload_ids)):
            return Response({"error": "The same time entry appears more than once."}, status=400)

        try:
            with transaction.atomic():
                return self._revise(request, lem, rows, payload_ids)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        except _PdfFailed as exc:
            return Response({"error": f"PDF generation failed: {exc}"}, status=500)

    def _revise(self, request, lem, rows, payload_ids):
        # Lock the ticket so two admins revising at once get R01 and R02, not two R01s.
        lem = (
            LEMReport.objects.select_for_update(of=("self",))
            .select_related("project__client", "project__workspace", "task", "requester")
            .get(pk=lem.pk)
        )
        existing = {
            e.id: e for e in lem.time_entries.filter(is_deleted=False).select_related("user", "job_title")
        }
        if not existing:
            raise ValueError(
                "This ticket has no linked time entries (it was issued before entries were tracked), "
                "so it can't be revised. Delete it and enter it again."
            )
        unknown = [str(i) for i in payload_ids if i not in existing]
        if unknown:
            raise ValueError("Some entries don't belong to this ticket (it may have been revised in the meantime). Reopen it and try again.")

        # Capture the ticket as it stands the first time it is revised, so the original stays downloadable.
        if not lem.revisions.exists():
            original = LEMRevision.objects.create(
                lem=lem, revision=lem.revision, report_data=lem.report_data,
                time_entry_ids=[str(i) for i in existing], created_by=lem.requester,
            )
            LEMRevision.objects.filter(pk=original.pk).update(created_at=lem.created_at)

        final_ids = []
        approvals = {}
        for index, row in enumerate(rows):
            row = dict(row)
            entry_id = row.pop("id", None)
            try:
                if entry_id:
                    for approval in update_field_ticket_entry(request, existing[entry_id], row):
                        approvals[approval.pk] = approval
                    final_ids.append(entry_id)
                else:
                    final_ids.append(create_field_ticket_entry(request, row).id)
            except ValueError as exc:
                raise ValueError(f"Entry {index}: {exc}") from exc

        # Entries the admin took off the ticket come off the timesheet too.
        removed = [e for eid, e in existing.items() if eid not in set(final_ids)]
        for entry in removed:
            for usage in entry.asset_usages.all():
                usage.delete()
            for item in TimeEntryApprovalItem.objects.filter(time_entry=entry).select_related("approval"):
                approvals[item.approval_id] = item.approval
                item.delete()
            entry.delete()
        for approval in approvals.values():
            if TimeEntryApprovalItem.objects.filter(approval=approval).exists():
                sync_approval_total(approval)
            else:
                approval.delete()

        entries = list(
            TimeEntry.objects.filter(id__in=final_ids)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
            .order_by("start_time")
        )
        project, task = lem.project, lem.task
        previous = lem.report_data or {}
        new_revision = lem.revision + 1
        date_str = (lem.lem_date or rows[0]["date"]).strftime("%Y-%m-%d")
        pm_info = project.pm_info or {}
        labour_rows, equipment_rows, total_cost = _build_field_ticket_labour_and_equipment(entries)

        pdf_data = {
            "lem_number":       format_display_number(lem.lem_number, new_revision),
            "lem_date":         date_str,
            "company_address":  _address_to_lines(project.workspace.address or ""),
            "project_name":     project.name,
            "task_name":        task.name if task else "",
            "job_number":       project.job_code or "",
            "client":           project.client.name if project.client else "",
            "pm_name":          pm_info.get("name", ""),
            "pm_contact":       pm_info.get("email", ""),
            "pm_phone":         pm_info.get("phone", ""),
            "labour_rows":      labour_rows,
            "work_description": _merge_entry_descriptions(entries),
            "equipment_rows":   equipment_rows,
            "total_cost":       f"${total_cost:,.2f}",
            "client_rep":       previous.get("client_rep", ""),
            "sign":             previous.get("sign", False),
            "sign_name":        previous.get("sign_name", ""),
            "sign_date":        previous.get("sign_date", ""),
        }
        # A revision stays on the ticket's BOSS template unless a different one is asked for.
        template = request.data.get("template") or lem.template or previous.get("boss_template") or DEFAULT_TEMPLATE
        pdf_data["boss_template"] = template
        pdf_data["boss"] = build_boss_ticket_data(entries, project, task, prepared_by=lem.requester)
        try:
            pdf_buffer, filename = _field_ticket_pdf(lem, pdf_data)
        except Exception as exc:
            raise _PdfFailed(str(exc)) from exc

        lem.report_data = pdf_data
        lem.revision = new_revision
        lem.template = template
        lem.save()
        lem.time_entries.set(entries)
        LEMRevision.objects.create(
            lem=lem, revision=new_revision, report_data=pdf_data,
            time_entry_ids=[str(e.id) for e in entries], created_by=request.user,
        )
        # A revision is a resubmission: it goes back to the consultant / contractor as pending.
        lem.set_status(LEMReport.STATUS_PENDING, request.user, f"Resubmitted as revision R{new_revision:02d}")

        response = FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )
        response["X-LEM-Display-Number"] = lem.ticket_number
        return response


class _PdfFailed(Exception):
    pass


class EnvisionFieldTicketHistoryView(APIView):
    """
    GET /api/reports/envision/lem/history/?lem_number=FT-000012

    Every issued version of the ticket, newest first (the current one flagged). Download any of them with
    GET lem/download/?lem_number=...&revision=N. Same visibility as the download endpoint.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def get(self, request):
        lem = _find_field_ticket_lem(request.GET.get("lem_number"), request.user)
        if not lem:
            return Response({"error": "Field ticket not found."}, status=404)
        if not _can_view_lem(request.user, lem):
            return Response({"error": "You do not have permission to view this field ticket."}, status=403)

        stored = {r.revision: r for r in lem.revisions.select_related("created_by")}
        versions = []
        for number in range(lem.revision, -1, -1):
            snapshot = stored.get(number)
            is_current = number == lem.revision
            data = lem.report_data if is_current else (snapshot.report_data if snapshot else {})
            by = snapshot.created_by if snapshot else lem.requester
            versions.append({
                "revision": number,
                "display_number": lem.ticket_number_for(number),
                "is_current": is_current,
                "created_at": snapshot.created_at if snapshot else lem.created_at,
                "created_by": (by.get_full_name() or by.email) if by else "-",
                "total_hours": _fmt_money(_sum_labour_hours(data)),
                "downloadable": is_current or bool(snapshot),
            })
        status_events = [
            {
                "status": event.status,
                "status_label": event.get_status_display(),
                "note": event.note,
                "revision": event.revision,
                "display_number": lem.ticket_number_for(event.revision),
                "changed_by": (event.changed_by.get_full_name() or event.changed_by.email) if event.changed_by else "-",
                "created_at": event.created_at,
            }
            for event in lem.status_events.select_related("changed_by").order_by("-created_at", "-id")
        ]
        return Response({
            "lem_number": lem.lem_number,
            "display_number": lem.ticket_number,
            "revision": lem.revision,
            **_lem_status_fields(lem),
            "versions": versions,
            "status_events": status_events,
        })


# ─────────────────────────────────────────────────────────────────────────────
#  Costing LEM
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_money(v):
    """Return a clean number string — int if whole, 2dp otherwise."""
    try:
        f = float(str(v or 0).replace(",", ""))
        return str(int(f)) if f == int(f) else f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(v or "0")


class _EnvisionCostingLEMDataMixin:
    """
    Shared data-building logic for the Costing LEM PDF and Excel exports.

    Both export formats accept the identical request parameters and must
    reuse the identical CT- LEM number for the same project + task +
    date_from — routing both formats through this one method guarantees
    that (same query, same code path), rather than merely hoping two
    independent implementations agree.

    Required body params:
        project_id  (uuid)  — project to report on
        date_from   (str)   — start date, YYYY-MM-DD
        date_to     (str)   — end date,   YYYY-MM-DD

    Optional body params:
        task_id     (uuid)  — filter to a specific task within the project
        client_rep  (str)
        sign        (bool)
        sign_name   (str)
        sign_date   (str)   — YYYY-MM-DD

    LEM prefix: CT- (Costing Ticket), workspace-wide sequential.
    Same project + task + date_from reuses the existing LEM number, but the
    report content is always rebuilt from current data — never served from cache.

    Rows are driven entirely by AssetUsage — job titles play no part in this
    report. Each row is one (TimeEntry, AssetUsage) pair; an entry with no
    linked asset contributes nothing, an entry linked to several assets
    contributes one row per asset. Work Type/grouping/subtotals are the
    linked asset's name, and Rate/Total are always recomputed from
    Hrs-Units × the asset's own rate.
    """

    CT_PREFIX = "CT-"

    def _build_costing_data(self, request):
        """Returns (error_response, ctx). ctx is None if error_response is set.

        ctx keys: data (the report data dict), lem_report, is_new, ct_display, from_str.
        """
        data = request.data

        # ── Required params ───────────────────────────────────────────────────
        project_id = data.get("project_id")
        task_id    = data.get("task_id")
        from_str   = data.get("date_from")
        to_str     = data.get("date_to")

        if not all([project_id, from_str, to_str]):
            return Response(
                {"error": "project_id, date_from and date_to are required"},
                status=400,
            ), None

        try:
            date_from = datetime.strptime(from_str, "%Y-%m-%d").date()
            date_to   = datetime.strptime(to_str,   "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400), None

        if date_to < date_from:
            return Response({"error": "date_to must be on or after date_from"}, status=400), None

        # ── Fetch project ─────────────────────────────────────────────────────
        try:
            project = Project.objects.select_related("client", "workspace").get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=404), None

        workspace = project.workspace

        # ── Fetch task (optional) ─────────────────────────────────────────────
        task = None
        if task_id:
            try:
                task = Task.objects.get(id=task_id, project=project)
            except Task.DoesNotExist:
                return Response({"error": "Task not found for this project"}, status=404), None

        # ── Build address / PM info ───────────────────────────────────────────
        pm_info       = project.pm_info or {}
        pm_name       = pm_info.get("name", "")
        pm_contact    = pm_info.get("email", "")
        pm_phone      = pm_info.get("phone", "")
        address_lines = _address_to_lines(workspace.address or "")

        # ── Fetch time entries ────────────────────────────────────────────────
        # date_from/date_to are Mountain-Time (Envision GEO) calendar days —
        # match them against UTC-stored start_time using explicit MT bounds.
        range_start, _ = envision_day_bounds_utc(date_from)
        _, range_end = envision_day_bounds_utc(date_to)
        entries_qs = (
            TimeEntry.objects
            .filter(
                project_id=project_id,
                start_time__gte=range_start,
                start_time__lte=range_end,
            )
            .select_related("user", "job_title", "task")
            .prefetch_related("asset_usages__asset")
            .order_by("job_title__name", "user__first_name", "user__last_name", "start_time")
        )
        if task_id:
            entries_qs = entries_qs.filter(task_id=task_id)

        if not entries_qs.exists():
            return Response(
                {"error": "No time entries found for the given project and date range."},
                status=404,
            ), None

        # ── Build labour groups from asset usage — Work Type is the linked
        # asset's name, never job_title (job titles play no part in this
        # report). An entry with no linked AssetUsage contributes no row;
        # an entry linked to several assets contributes one row per asset,
        # each using that AssetUsage's own quantity_used (never the entry's
        # full duration, so hours aren't double-counted across rows). Rate
        # and Total are always recomputed from Hrs/Units × the asset's own
        # rate, never trusted from a stored AssetUsage.cost value. Grouping
        # and subtotals are keyed by asset name — the "job_title" dict key
        # below is reused as-is (holding the asset name) purely so the PDF
        # and Excel renderers, which only treat it as a label string, need
        # no changes.
        asset_usage_pairs = [
            (entry, usage)
            for entry in entries_qs
            for usage in entry.asset_usages.all()
        ]
        asset_usage_pairs.sort(
            key=lambda pair: (pair[1].asset.name.lower(), utc_to_envision_local(pair[0].start_time))
        )

        if not asset_usage_pairs:
            return Response(
                {"error": "No asset usage found for the given project and date range."},
                status=404,
            ), None

        labour_map  = OrderedDict()
        grand_total = Decimal("0")
        included_entry_ids = set()

        for entry, usage in asset_usage_pairs:
            asset     = usage.asset
            emp_name  = entry.user.get_full_name() or entry.user.email
            task_name = entry.task.name if entry.task else ""

            # quantity_used is the authoritative Hrs/Units value when set;
            # otherwise fall back to the entry's own duration.
            if usage.quantity_used is not None:
                hrs_units = Decimal(usage.quantity_used)
            else:
                hrs_units = Decimal(entry.duration or 0) / 60

            if asset.charge_type == "hourly":
                rate = Decimal(asset.hourly_rate or 0)
            else:
                rate = Decimal(asset.quantity_rate or 0)
            line_total = round(hrs_units * rate, 2)

            key = asset.id
            if key not in labour_map:
                labour_map[key] = {
                    "job_title":   asset.name,
                    "entries":     [],
                    "hours_total": Decimal("0"),
                    "subtotal":    Decimal("0"),
                }

            labour_map[key]["entries"].append({
                "employee":    emp_name,
                "date":        utc_to_envision_local(entry.start_time).strftime("%b %d, %Y"),
                "task":        task_name,
                "description": (entry.description or "").strip(),
                "hours":       _fmt_money(round(hrs_units, 2)),
                "rate":        _fmt_money(rate),
                "total":       _fmt_money(line_total),
            })
            labour_map[key]["hours_total"] += hrs_units
            labour_map[key]["subtotal"]    += line_total
            grand_total += line_total
            included_entry_ids.add(entry.id)

        labour_groups = []
        for grp in labour_map.values():
            labour_groups.append({
                "job_title":   grp["job_title"],
                "entries":     grp["entries"],
                "hours_total": _fmt_money(round(grp["hours_total"], 2)),
                "subtotal":    _fmt_money(grp["subtotal"]),
            })

        # No separate Asset section any more — every asset usage is folded
        # directly into labour_groups above.
        asset_rows  = []
        asset_total = Decimal("0")

        # ── Reuse the CT LEM number for this project+task+date_from, or assign a new one ──
        lem_report = (
            LEMReport.objects
            .filter(
                project=project,
                task=task,
                lem_date=date_from,
                lem_number__startswith=self.CT_PREFIX,
            )
            .first()
        )
        is_new = lem_report is None
        if is_new:
            # all_objects — a voided LEM's number must never be reused (unique_together).
            last_ct = (
                LEMReport.all_objects
                .filter(project__workspace=workspace, lem_number__startswith=self.CT_PREFIX)
                .order_by("-id")
                .first()
            )
            try:
                last_num = int(last_ct.lem_number.replace(self.CT_PREFIX, "")) if last_ct else 0
            except ValueError:
                last_num = 0

            lem_report = LEMReport(
                requester=request.user,
                project=project,
                task=task,
                lem_date=date_from,
                lem_number=f"{self.CT_PREFIX}{last_num + 1:06d}",
                report_data={},
            )
            lem_report.save()

        ct_display = lem_report.lem_number.replace(self.CT_PREFIX, "")

        # ── Date range label ──────────────────────────────────────────────────
        if date_from == date_to:
            date_label = date_from.strftime("%b %d, %Y")
        else:
            date_label = "{} – {}".format(
                date_from.strftime("%b %d, %Y"),
                date_to.strftime("%b %d, %Y"),
            )

        # ── Signature fields ──────────────────────────────────────────────────
        sign      = data.get("sign", False)
        sign_name = data.get("sign_name") or request.user.get_full_name()
        sign_date = data.get("sign_date") or to_str

        pdf_data = {
            "lem_number":      ct_display,
            "lem_date":        date_label,
            "company_address": address_lines,
            "project_name":    project.name,
            "task_name":       task.name if task else "",
            "job_number":      project.job_code or "",
            "client":          project.client.name if project.client else "",
            "pm_name":         pm_name,
            "pm_contact":      pm_contact,
            "pm_phone":        pm_phone,
            "labour_groups":   labour_groups,
            "asset_rows":      asset_rows,
            "asset_total":     _fmt_money(asset_total),
            "grand_total":     _fmt_money(grand_total),
            "client_rep":      data.get("client_rep", ""),
            "sign":            sign,
            "sign_name":       sign_name if sign else "",
            "sign_date":       sign_date if sign else "",
        }

        # ── Save fresh report data onto the (reused or new) LEM record ────────
        lem_report.report_data = pdf_data
        lem_report.save()
        # Record exactly which entries this snapshot was built from — voiding
        # this LEM later only touches these, not every entry that happens to
        # match the same project/task/date range. Only entries that actually
        # contributed a row above (i.e. had a linked AssetUsage) are included.
        lem_report.time_entries.set(entries_qs.filter(id__in=included_entry_ids))

        return None, {
            "data":       pdf_data,
            "lem_report": lem_report,
            "is_new":     is_new,
            "ct_display": ct_display,
            "from_str":   from_str,
        }


class EnvisionCostingLEMView(_EnvisionCostingLEMDataMixin, APIView):
    """Generate and download an Envision GEO Costing LEM PDF. See
    _EnvisionCostingLEMDataMixin for the shared request contract."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        error, ctx = self._build_costing_data(request)
        if error:
            return error

        try:
            pdf_buffer = generate_envision_costing_pdf(ctx["data"])
        except Exception as exc:
            if ctx["is_new"]:
                ctx["lem_report"].delete()
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Envision_Costing_LEM_{ctx['ct_display']}_{ctx['from_str']}.pdf",
            content_type="application/pdf",
        )


class EnvisionCostingLEMExcelView(_EnvisionCostingLEMDataMixin, APIView):
    """Generate and download an Envision GEO Costing LEM Excel workbook.
    Accepts the identical request parameters as EnvisionCostingLEMView (PDF)
    and reuses the identical CT- LEM number for the same project + task +
    date_from — see _EnvisionCostingLEMDataMixin."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        error, ctx = self._build_costing_data(request)
        if error:
            return error

        try:
            xlsx_buffer = generate_envision_costing_xlsx(ctx["data"])
        except Exception as exc:
            if ctx["is_new"]:
                ctx["lem_report"].delete()
            return Response({"error": f"Excel generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            xlsx_buffer,
            as_attachment=True,
            filename=f"Envision_Costing_LEM_{ctx['ct_display']}_{ctx['from_str']}.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
