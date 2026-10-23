"""
Blank Daily Ticket (paper LEM) download.
  POST /api/reports/daily-ticket/download/  — reserve the next ticket number and return the blank PDF
  GET  /api/reports/daily-ticket/downloads/ — who downloaded which ticket number, and when (admins)
"""

from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils.envision_time import ENVISION_TZ
from workspaces.models import Workspace, WorkspaceMember
from .daily_ticket_pdf_utils import generate_daily_ticket_pdf
from .models import DailyTicketDownload


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _resolve_workspace(user, workspace_id):
    """The workspace the ticket is issued under: the one asked for if the user belongs to it
    (superusers: any), otherwise the user's first active membership."""
    memberships = WorkspaceMember.objects.filter(user=user, is_active=True)
    if workspace_id:
        if user.is_superuser:
            return Workspace.objects.filter(id=workspace_id).first()
        member = memberships.filter(workspace_id=workspace_id).select_related("workspace").first()
        if member:
            return member.workspace
    member = memberships.select_related("workspace").first()
    return member.workspace if member else None


class DailyTicketDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        workspace = _resolve_workspace(request.user, request.data.get("workspace"))

        def build_pdf(ticket):
            local_date = ticket.downloaded_at.astimezone(ENVISION_TZ).date()
            return generate_daily_ticket_pdf(
                ticket.ticket_number, f"{local_date:%b} {local_date.day}, {local_date.year}",
            )

        # The PDF is built inside the number-reservation transaction, so a failed download never burns a
        # ticket number. Kept to a single transaction (no extra savepoints): every query is a round trip
        # to the database.
        ticket, pdf_buffer = DailyTicketDownload.issue(
            user=request.user, workspace=workspace, ip_address=_client_ip(request), on_issued=build_pdf,
        )

        response = FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Daily_Ticket_{ticket.ticket_number}.pdf",
            content_type="application/pdf",
        )
        response["X-Daily-Ticket-Number"] = ticket.ticket_number
        response["Access-Control-Expose-Headers"] = "X-Daily-Ticket-Number, Content-Disposition"
        return response


class DailyTicketDownloadListView(APIView):
    permission_classes = [IsAuthenticated]
    MAX_ROWS = 500

    def get(self, request):
        user = request.user
        qs = DailyTicketDownload.objects.select_related("downloaded_by", "workspace")
        if not user.is_superuser:
            admin_workspace_ids = WorkspaceMember.objects.filter(
                user=user, role="admin", is_active=True,
            ).values_list("workspace_id", flat=True)
            qs = qs.filter(workspace_id__in=admin_workspace_ids)

        rows = [
            {
                "ticket_number": t.ticket_number,
                "downloaded_at": t.downloaded_at,
                "downloaded_by": t.downloaded_by.get_full_name() or t.downloaded_by.email if t.downloaded_by else "",
                "downloaded_by_email": t.downloaded_by.email if t.downloaded_by else "",
                "workspace": t.workspace.name if t.workspace else "",
            }
            for t in qs[: self.MAX_ROWS]
        ]
        return Response(rows)
