"""
Envision GEO — LEM Report API Views
  POST /api/reports/envision/fieldTicket_Lem/           — Field Ticket LEM PDF (whole day)
  POST /api/reports/envision/fieldTicket_Lem/payload/   — Field Ticket LEM PDF (payload entries only)
  POST /api/reports/envision/costing-lem/                — Costing LEM PDF
  GET  /api/reports/envision/lem/search/                 — Search Field Ticket LEM by number
  POST /api/reports/envision/lem/void/                   — Void a LEM (soft-delete LEM + its time entries)
"""

from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from core.utils.envision_time import envision_day_bounds_utc, utc_to_envision_local
from projects.models import Project
from tasks.models import Task
from time_entries.field_ticket_views import create_field_ticket_entry
from time_entries.models import TimeEntry
from time_entries.serializers import FieldTicketEntrySerializer
from workspaces.models import WorkspaceMember
from workspaces.permissions import IsWorkspaceUser
from .models import LEMReport
from .envision_pdf_utils import generate_envision_lem_pdf
from .envision_costing_pdf_utils import generate_envision_costing_pdf
from .envision_costing_excel_utils import generate_envision_costing_xlsx


def _address_to_lines(address: str) -> list:
    """Split a comma-separated address string into PDF lines."""
    return [part.strip() for part in (address or "").split(",") if part.strip()]


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
            cost  = Decimal(usage.cost or 0)
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

        ft_display_number = lem_report.lem_number.replace(FT_DB_PREFIX, "")

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

        lem_report.report_data = pdf_data
        lem_report.save()
        # Record exactly which entries this snapshot was built from — voiding
        # this LEM later only touches these, not every entry that happens to
        # match the same project/task/date.
        lem_report.time_entries.set(entries)

        try:
            pdf_buffer = generate_envision_lem_pdf(pdf_data)
        except Exception as exc:
            if is_new:
                lem_report.delete()
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Envision_LEM_{ft_display_number}_{date_str}.pdf",
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

        lem_report.report_data = pdf_data
        lem_report.save()
        # Record exactly these entries — this is the whole point of this
        # endpoint, so voiding it later only ever touches this submission.
        lem_report.time_entries.set(entries)

        try:
            pdf_buffer = generate_envision_lem_pdf(pdf_data)
        except Exception as exc:
            lem_report.delete()
            return Response({"error": f"PDF generation failed: {str(exc)}"}, status=500)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Envision_LEM_{ft_display_number}_{date_str}.pdf",
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

        # Normalise: strip FTF- prefix if caller included it, then re-add
        digits = raw.replace(self.FTF_PREFIX, "").strip()
        db_key = f"{self.FTF_PREFIX}{digits}"

        try:
            report = LEMReport.objects.get(lem_number=db_key)
        except LEMReport.DoesNotExist:
            return Response(
                {"error": f"No Field Ticket LEM found for number '{digits}'"},
                status=404,
            )

        return Response({
            "lem_number":  digits,
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
    new LEM.

    ONLY works on FTF- (Field Ticket Field, from
    EnvisionFieldTicketLEMFromPayloadView) numbers — FT- (whole-day) and
    CT- (Costing) LEMs cannot be voided through this endpoint.

    Required body params:
        lem_number  (str)  — with or without the FTF- prefix

    Restricted to admins/managers/field_managers (or superusers) within the
    workspace that owns the LEM's project.

    Note: LEMs generated before the `time_entries` relation existed have
    nothing recorded on it, so voiding one soft-deletes the LEMReport but
    no time entries — there's no reliable way to know which ones were
    "this" LEM's after the fact.
    """

    permission_classes = [IsAuthenticated]
    ELEVATED_ROLES = {"admin", "manager", "field_manager"}

    def post(self, request):
        raw = str(request.data.get("lem_number", "")).strip()
        if not raw:
            return Response({"error": "lem_number is required"}, status=400)

        lem_report = self._find_lem(raw)
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

    def _find_lem(self, raw):
        """Only matches FTF- (Field Ticket Field / payload) LEMs — strips
        whichever known prefix (if any) was passed in, then looks up FTF-
        only. FT-/CT-/LEM- numbers are out of scope for this endpoint, even
        if that prefix is what was actually passed."""
        digits = raw
        for prefix in (self.FTF_PREFIX, "FT-", "CT-", "LEM-"):
            if digits.startswith(prefix):
                digits = digits[len(prefix):]
                break
        db_key = f"{self.FTF_PREFIX}{digits.strip()}"
        return LEMReport.objects.filter(lem_number=db_key).first()


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
