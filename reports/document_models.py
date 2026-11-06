"""
Invoices and the documents kept with field tickets (stored in S3, see reports.storage).

  * Invoice: one invoice to a client, covering one or more field tickets (LEMReport.invoice). Today it records the
    number and date an admin enters when marking tickets invoiced, plus the invoice file they upload. It's also the
    place invoices built inside the app will live (lines = its tickets with their numbers and amounts, `total`).
  * LEMDocument: one stored file —
      ticket_pdf         the PDF of a field ticket as issued (every revision), uploaded automatically
      verification       a PDF / photo proving the ticket was accepted (e.g. the client's signed copy)
      invoice            the invoice document, attached to an Invoice
      signed_ticket_pdf  a ticket PDF signed inside the app (reserved for the coming digital signatures)
"""

import os
import re
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import SoftDeleteModel
from .storage import document_storage


class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=50)
    invoiced_on = models.DateField()
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # For invoices built in the app: the sum of its tickets' amounts (and any adjustments). Empty for uploaded ones.
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("workspace", "number")]
        ordering = ["-invoiced_on", "-created_at"]

    def __str__(self):
        return self.number


def _safe(name):
    """A file / folder name that's safe in a storage key."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-.") or "file"


def document_path(instance, filename):
    """<workspace>/field-tickets/<ticket number>/<kind>/<time>-<file>, or <workspace>/invoices/<number>/... ."""
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{_safe(os.path.basename(filename))}"
    if instance.invoice_id and not instance.lem_id:
        invoice = instance.invoice
        return f"{invoice.workspace_id}/invoices/{_safe(invoice.number)}/{name}"
    lem = instance.lem
    workspace_id = lem.project.workspace_id if lem.project_id else "no-workspace"
    return f"{workspace_id}/field-tickets/{_safe(lem.ticket_number_for(0))}/{instance.kind}/{name}"


class LEMDocument(SoftDeleteModel):
    TICKET_PDF = "ticket_pdf"
    VERIFICATION = "verification"
    INVOICE = "invoice"
    SIGNED_TICKET_PDF = "signed_ticket_pdf"
    KIND_CHOICES = (
        (TICKET_PDF, "Field ticket PDF"),
        (VERIFICATION, "Verification document"),
        (INVOICE, "Invoice"),
        (SIGNED_TICKET_PDF, "Signed field ticket PDF"),
    )
    # Kinds people upload (the others the app makes itself).
    UPLOADED_KINDS = (VERIFICATION, INVOICE)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lem = models.ForeignKey("reports.LEMReport", on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    # Which version of the ticket a generated / signed PDF is (0 = as first issued, 1 = R01 ...).
    revision = models.PositiveIntegerField(null=True, blank=True)
    file = models.FileField(storage=document_storage, upload_to=document_path, max_length=500)
    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["lem", "kind"], name="lemdocument_lem_kind_idx")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.original_name}"
