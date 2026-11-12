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
    `kind` is where it came from; `document_type` is what the document is (LEM, Approved LEM, Invoice, Others),
    shown with it and used as its folder in storage. The type is set automatically from where it was uploaded
    (approving a ticket -> Approved LEM, marking it invoiced -> Invoice, the issued PDF -> LEM) and chosen by the
    person uploading from a ticket's Documents dialog.
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
    """<field ticket UUID>/<document type>_<time>_<file>, inside the bucket's folder (AWS_DOCUMENTS_PREFIX, e.g.
    boss_workpulse/): every file of a ticket sits in that ticket's own folder. An invoice file is kept once, in the
    folder of the first ticket on the invoice (lowest ticket number)."""
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    document_type = (instance.document_type or LEMDocument.DEFAULT_TYPE[instance.kind]).replace("_", "-")
    name = f"{document_type}_{stamp}_{_safe(os.path.basename(filename))}"
    lem_id = instance.lem_id
    if not lem_id and instance.invoice_id:
        from .models import LEMReport

        lem_id = (LEMReport.all_objects.filter(invoice_id=instance.invoice_id).order_by("lem_number")
                  .values_list("id", flat=True).first())
        if not lem_id:  # an invoice with no tickets (not something the app makes): its own folder
            return f"invoices/{instance.invoice_id}/{name}"
    return f"{lem_id}/{name}"


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

    # What the document is.
    TYPE_LEM = "lem"
    TYPE_APPROVED_LEM = "approved_lem"
    TYPE_INVOICE = "invoice"
    TYPE_OTHER = "other"
    DOCUMENT_TYPE_CHOICES = (
        (TYPE_LEM, "LEM"),
        (TYPE_APPROVED_LEM, "Approved LEM"),
        (TYPE_INVOICE, "Invoice"),
        (TYPE_OTHER, "Others"),
    )
    # The type a document gets from where it came from, when nobody chose one.
    DEFAULT_TYPE = {
        TICKET_PDF: TYPE_LEM,
        VERIFICATION: TYPE_APPROVED_LEM,
        INVOICE: TYPE_INVOICE,
        SIGNED_TICKET_PDF: TYPE_LEM,
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lem = models.ForeignKey("reports.LEMReport", on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES, default=TYPE_OTHER)
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

    def __init__(self, *args, **kwargs):
        # No type given: the one its kind implies (an issued PDF is a LEM, an invoice upload an Invoice, ...).
        if "document_type" not in kwargs and kwargs.get("kind") in self.DEFAULT_TYPE:
            kwargs["document_type"] = self.DEFAULT_TYPE[kwargs["kind"]]
        super().__init__(*args, **kwargs)

    def __str__(self):
        return f"{self.get_document_type_display()}: {self.original_name}"
