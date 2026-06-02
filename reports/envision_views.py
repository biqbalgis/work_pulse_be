"""
Envision GEO — Field Ticket LEM Report API Views
POST /api/reports/envision/fieldTicket_Lem/
"""

from datetime import datetime
from decimal import Decimal

from django.http import FileResponse
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from projects.models import Project
from time_entries.models import TimeEntry
from workspaces.permissions import IsWorkspaceUser
from .models import LEMReport
from .envision_pdf_utils import generate_envision_lem_pdf


def _address_to_lines(address: str) -> list:
    """Split a comma-separated address string into PDF lines."""
    return [part.strip() for part in (address or "").split(",") if part.strip()]


class EnvisionLEMReportView(APIView):
    """
    Generate and download an Envision GEO Field Ticket LEM PDF.

    Address, PM info, and job code are read from the database
    (Workspace.address, Workspace.pm_info, Project.job_code).

    Required body params:
        project_id       (uuid)  — project to report on
        date             (str)   — report date, YYYY-MM-DD

    Optional body params:
        client_rep       (str)   — client representative name
        work_description (str)   — auto-built from time entry descriptions if omitted
        sign             (bool)
        sign_name        (str)
        sign_date        (str)   — YYYY-MM-DD
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def post(self, request):
        data = request.data

        # ── Validate required fields ──────────────────────────────────────────
        project_id = data.get("project_id")
        date_str   = data.get("date")

        if not project_id or not date_str:
            return Response({"error": "project_id and date are required"}, status=400)

        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        try:
            project = Project.objects.select_related("client", "workspace").get(id=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found"}, status=404)

        # ── Pull workspace-level info from DB ─────────────────────────────────
        workspace  = project.workspace
        pm_info    = workspace.pm_info or {}          # {"pm_name": "", "contact": "", "phone": ""}
        pm_name    = pm_info.get("pm_name", "")
        pm_contact = pm_info.get("contact", "")
        pm_phone   = pm_info.get("phone", "")
        address_lines = _address_to_lines(workspace.address or "")

        # ── Fetch time entries ────────────────────────────────────────────────
        entries = (
            TimeEntry.objects
            .filter(project_id=project_id, start_time__date=report_date)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
            .order_by("start_time")
        )

        # ── Build labour rows ─────────────────────────────────────────────────
        # Aggregate per (user, job_title) so multiple entries per user collapse.
        labour_map = {}  # key: (user_id, job_title_id)
        for entry in entries:
            jt_name = entry.job_title.name if entry.job_title else "—"
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

        # ── Build equipment rows & total cost ─────────────────────────────────
        asset_map   = {}   # key: asset_id → accumulated row
        total_cost  = Decimal("0")

        # Personnel cost
        for entry in entries:
            total_cost += Decimal(entry.cost or 0)

        # Asset cost
        for entry in entries:
            for usage in entry.asset_usages.all():
                asset  = usage.asset
                cost   = Decimal(usage.cost or 0)
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
                    asset_map[asset.id]["hours"] += Decimal(entry.duration) / 60
                    asset_map[asset.id]["rate"]   = Decimal(asset.hourly_rate or 0)
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

        # ── Merge all time entry descriptions into one paragraph ─────────────
        # Deduplicate, strip whitespace, join with ". " so it reads naturally.
        seen = set()
        desc_parts = []
        for e in entries:
            text = (e.description or "").strip()
            if text and text not in seen:
                seen.add(text)
                desc_parts.append(text)
        work_description = (
            data.get("work_description")
            or (". ".join(desc_parts) + "." if desc_parts else "N/A")
        )

        # ── Generate LEM-FT-### number (separate sequence from standard LEMs) ──
        FT_PREFIX = "LEM-FT-"
        last_ft = (
            LEMReport.objects
            .filter(project=project, lem_number__startswith=FT_PREFIX)
            .order_by("-id")
            .first()
        )
        if last_ft:
            try:
                last_num = int(last_ft.lem_number.split("-")[-1])
            except (ValueError, IndexError):
                last_num = 0
        else:
            last_num = 0
        ft_lem_number = f"{FT_PREFIX}{last_num + 1:03d}"

        lem_report = LEMReport(
            requester=request.user,
            project=project,
            report_data={},
        )
        # Bypass the model's auto-generate by setting lem_number before save
        lem_report.lem_number = ft_lem_number
        lem_report.save()

        # ── Signature fields (still caller-supplied) ──────────────────────────
        user      = request.user
        sign      = data.get("sign", False)
        sign_name = data.get("sign_name") or user.get_full_name()
        sign_date = data.get("sign_date") or date_str

        pdf_data = {
            "lem_number":       lem_report.lem_number,
            "lem_date":         date_str,
            # ── From DB ───────────────────────────────────────────────────────
            "company_address":  address_lines,
            "project_name":     project.name,
            "job_number":       project.job_code or "",
            "client":           project.client.name if project.client else "",
            "pm_name":          pm_name,
            "pm_contact":       pm_contact,
            "pm_phone":         pm_phone,
            # ── Built from time entries ───────────────────────────────────────
            "labour_rows":      labour_rows,
            "work_description": work_description,
            "equipment_rows":   equipment_rows,
            "total_cost":       f"${total_cost:,.2f}",
            # ── Caller-supplied ───────────────────────────────────────────────
            "client_rep":       data.get("client_rep", ""),
            "sign":             sign,
            "sign_name":        sign_name if sign else "",
            "sign_date":        sign_date if sign else "",
        }

        # Save report snapshot
        lem_report.report_data = pdf_data
        lem_report.save()

        pdf_buffer = generate_envision_lem_pdf(pdf_data)

        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Envision_LEM_{lem_report.lem_number}_{date_str}.pdf",
            content_type="application/pdf",
        )
