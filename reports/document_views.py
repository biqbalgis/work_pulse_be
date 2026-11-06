"""
Documents kept with field tickets and invoices (stored in S3, see reports.storage / reports.document_models).

    GET    /api/reports/envision/lem/documents/?lem_number=FTF-000012   a ticket's documents (and its invoice's)
    POST   /api/reports/envision/lem/documents/                         upload a verification document
           multipart: lem_number, file (PDF or image), kind=verification
    GET    /api/reports/envision/documents/<id>/download/               the file itself
    DELETE /api/reports/envision/documents/<id>/                        remove an uploaded document (admins)
"""

from django.db.models import Q
from django.http import FileResponse
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspaces.permissions import ADMIN_ONLY, has_workspace_role
from .document_models import LEMDocument
from .documents import document_json, save_upload
from .envision_views import _can_change_lem_status, _can_view_lem, _find_field_ticket_lem
from .models import LEMReport

# A verification document proves an accepted ticket, so it goes on approved (or already invoiced) tickets.
VERIFIABLE_STATUSES = (LEMReport.STATUS_APPROVED, LEMReport.STATUS_INVOICED)


def _documents_of(lem):
    return (
        LEMDocument.objects.filter(Q(lem=lem) | Q(invoice_id=lem.invoice_id, invoice__isnull=False))
        .select_related("uploaded_by", "invoice")
        .order_by("-created_at")
    )


def _can_open(user, document):
    """Ticket documents: whoever can see the ticket. Invoice documents: admins of the invoice's workspace, or anyone
    who can see one of its tickets."""
    if user.is_superuser:
        return True
    if document.lem_id:
        return _can_view_lem(user, document.lem)
    invoice = document.invoice
    if has_workspace_role(user, invoice.workspace_id, ADMIN_ONLY):
        return True
    return any(_can_view_lem(user, lem) for lem in invoice.tickets.filter(is_deleted=False).select_related("project"))


def _workspace_of(document):
    if document.lem_id:
        return document.lem.project.workspace_id if document.lem.project_id else None
    return document.invoice.workspace_id


class LEMDocumentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lem = _find_field_ticket_lem(str(request.query_params.get("lem_number", "")), request.user)
        if not lem or not _can_view_lem(request.user, lem):
            return Response({"error": "Field ticket not found."}, status=404)
        return Response({
            "lem_number": lem.lem_number,
            "ticket_number": lem.ticket_number,
            "status": lem.status,
            "can_upload_verification": lem.status in VERIFIABLE_STATUSES and _can_change_lem_status(request.user, lem),
            "documents": [document_json(d) for d in _documents_of(lem)],
        })

    def post(self, request):
        lem = _find_field_ticket_lem(str(request.data.get("lem_number", "")), request.user)
        if not lem or not _can_view_lem(request.user, lem):
            return Response({"error": "Field ticket not found."}, status=404)
        kind = request.data.get("kind") or LEMDocument.VERIFICATION
        if kind != LEMDocument.VERIFICATION:
            raise ValidationError("Only verification documents can be uploaded to a field ticket.")
        if not _can_change_lem_status(request.user, lem):
            return Response({"error": "Only people who approve field tickets can add verification documents."}, status=403)
        if lem.status not in VERIFIABLE_STATUSES:
            raise ValidationError(f"{lem.ticket_number} isn't approved yet. Add the verification document once it's approved.")
        document = save_upload(request.FILES.get("file"), LEMDocument.VERIFICATION, request.user, lem=lem)
        return Response(document_json(document), status=201)


class DocumentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        document = (LEMDocument.objects.select_related("lem__project", "invoice", "uploaded_by")
                    .filter(pk=pk).first())
        if not document or not _can_open(request.user, document):
            return None
        return document

    def get(self, request, pk):
        document = self._get(request, pk)
        if not document:
            return Response({"error": "Document not found."}, status=404)
        try:
            handle = document.file.open("rb")
        except (FileNotFoundError, OSError):
            return Response({"error": "This document's file can't be found in storage."}, status=404)
        return FileResponse(handle, as_attachment=True, filename=document.original_name or "document",
                            content_type=document.content_type or "application/octet-stream")

    def delete(self, request, pk):
        document = self._get(request, pk)
        if not document:
            return Response({"error": "Document not found."}, status=404)
        if document.kind not in LEMDocument.UPLOADED_KINDS:
            raise ValidationError("Issued field ticket PDFs are kept as the record and can't be removed.")
        workspace_id = _workspace_of(document)
        if not (request.user.is_superuser or (workspace_id and has_workspace_role(request.user, workspace_id, ADMIN_ONLY))):
            return Response({"error": "Only workspace admins can remove documents."}, status=403)
        document.delete()  # soft delete: the file stays in S3, out of the lists
        return Response(status=204)
