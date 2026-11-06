"""
Upload the PDF of every field ticket that has none stored for its current revision (S3, see reports.storage):
tickets issued before PDFs were kept, or ones whose upload failed at the time.

    python manage.py store_ticket_pdfs              # every field ticket missing its PDF
    python manage.py store_ticket_pdfs --dry-run    # list them only

New tickets and revisions store their PDF as they're issued; this only fills the gaps. The PDF is rebuilt from the
ticket's saved data, so it's the same document the app downloads.
"""

from django.core.management.base import BaseCommand

from reports.document_models import LEMDocument
from reports.documents import store_ticket_pdf
from reports.envision_views import _field_ticket_pdf
from reports.models import LEMReport
from reports.storage import storage_name


class Command(BaseCommand):
    help = "Store the PDF of field tickets that don't have one stored yet."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the tickets without uploading anything.")

    def handle(self, *args, **opts):
        tickets = LEMReport.objects.filter(lem_number__startswith="FTF-").select_related("project").order_by("id")
        missing = [
            lem for lem in tickets
            if not LEMDocument.objects.filter(lem=lem, kind=LEMDocument.TICKET_PDF, revision=lem.revision).exists()
        ]
        self.stdout.write(f"{len(missing)} field tickets have no stored PDF ({storage_name()}).")
        stored = failed = 0
        for lem in missing:
            if opts["dry_run"]:
                self.stdout.write(f"  {lem.ticket_number}")
                continue
            try:
                buffer, filename = _field_ticket_pdf(lem, lem.report_data or {})
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.stdout.write(self.style.WARNING(f"  {lem.ticket_number}: couldn't build the PDF ({exc})"))
                continue
            if store_ticket_pdf(lem, buffer.getvalue(), filename, lem.requester):
                stored += 1
            else:
                failed += 1
                self.stdout.write(self.style.WARNING(f"  {lem.ticket_number}: upload failed (see the log)"))
        if not opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"Stored {stored} PDFs; {failed} failed."))
