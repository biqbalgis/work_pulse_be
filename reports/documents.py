"""Storing field ticket documents (see reports.document_models / reports.storage)."""

import logging
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .document_models import LEMDocument

logger = logging.getLogger(__name__)

# What people may upload as a verification document or invoice: PDFs and photos / scans.
ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


def validate_upload(upload):
    """Raise a readable 400 unless `upload` is a PDF or image within the size limit."""
    if upload is None:
        raise ValidationError("Choose a PDF or image file to upload.")
    extension = os.path.splitext(upload.name or "")[1].lower()
    if extension not in ALLOWED_TYPES:
        raise ValidationError("Only PDF or image files (PNG, JPG, WEBP, HEIC) can be uploaded.")
    limit_mb = settings.DOCUMENT_MAX_UPLOAD_MB
    if upload.size > limit_mb * 1024 * 1024:
        raise ValidationError(f"That file is {upload.size / 1024 / 1024:.1f} MB; the most you can upload is {limit_mb} MB.")
    if upload.size == 0:
        raise ValidationError("That file is empty.")
    return ALLOWED_TYPES[extension]


def save_upload(upload, kind, user, lem=None, invoice=None):
    """Store an uploaded verification document / invoice file and record it."""
    content_type = validate_upload(upload)
    document = LEMDocument(lem=lem, invoice=invoice, kind=kind, original_name=os.path.basename(upload.name)[:255],
                           content_type=content_type, size=upload.size, uploaded_by=user)
    document.file.save(upload.name, upload, save=False)
    document.save()
    return document


def store_ticket_pdf(lem, pdf_bytes, filename, user):
    """Keep a copy of a field ticket's PDF as issued (this revision). A storage problem is logged, never stops the
    ticket being saved: `python manage.py store_ticket_pdfs` uploads any that are missing."""
    try:
        document = LEMDocument(lem=lem, kind=LEMDocument.TICKET_PDF, revision=lem.revision, original_name=filename,
                               content_type="application/pdf", size=len(pdf_bytes), uploaded_by=user)
        document.file.save(filename, ContentFile(pdf_bytes), save=False)
        document.save()
        return document
    except Exception:  # noqa: BLE001 — the ticket matters more than its stored copy
        logger.exception("Could not store the PDF of field ticket %s", lem.lem_number)
        return None


def document_json(document):
    user = document.uploaded_by
    return {
        "id": str(document.id),
        "kind": document.kind,
        "kind_label": document.get_kind_display(),
        "name": document.original_name,
        "content_type": document.content_type,
        "size": document.size,
        "revision": document.revision,
        "invoice_number": document.invoice.number if document.invoice_id else "",
        "uploaded_by": (user.get_full_name() or user.email) if user else "",
        "uploaded_at": timezone.localtime(document.created_at).isoformat(),
        "download_url": f"/api/reports/envision/documents/{document.id}/download/",
    }
