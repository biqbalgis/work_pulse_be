
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core.models import SoftDeleteModel
from core.utils.envision_time import ENVISION_TZ

User = get_user_model()

class LEMReport(SoftDeleteModel):
    lem_number = models.CharField(max_length=20, editable=False)
    requester  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    project    = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True)
    task       = models.ForeignKey("tasks.Task", on_delete=models.SET_NULL, null=True, blank=True)
    lem_date   = models.DateField(null=True, blank=True)
    report_data = models.JSONField(default=dict)
    created_at  = models.DateTimeField(auto_now_add=True)

    # Who deleted/voided this LEM and when — deleted_at (SoftDeleteModel)
    # already records "when"; deleted_by fills in "who" for the same event.
    deleted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    # Exactly which TimeEntry rows this LEM's report_data was built from —
    # set (not just added to) every time the LEM is (re)generated, so voiding
    # only touches entries actually captured in the latest snapshot, not
    # every entry that happens to match the same project/task/date.
    time_entries = models.ManyToManyField(
        "time_entries.TimeEntry", blank=True, related_name="lem_reports",
    )

    # 0 = the ticket as first issued; incremented each time an admin revises it (see LEMRevision). lem_number
    # itself never changes � the revision is shown as a suffix, e.g. "000012-R01".
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [['project', 'lem_number']]
        indexes = [
            # Dashboard field-tickets report filters by project + lem_date range.
            models.Index(fields=['project', 'lem_date'], name='lemreport_project_date_idx'),
            # Several views filter on lem_number__startswith (FT-/FTF-/CT- series).
            models.Index(fields=['lem_number'], name='lemreport_lem_number_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self.lem_number:
            # Generate sequential number per project. Uses all_objects (not
            # objects) so a voided/soft-deleted LEM's number is never reused —
            # reusing it would collide with the unique_together constraint.
            if self.project:
                last_entry = LEMReport.all_objects.filter(
                    project=self.project
                ).order_by("-id").first()

                if last_entry and last_entry.lem_number.startswith("LEM-"):
                    try:
                        # Extract number from LEM-001 format
                        last_number = int(last_entry.lem_number.split('-')[1])
                        new_number = last_number + 1
                    except (ValueError, IndexError):
                        new_number = 1
                else:
                    new_number = 1
            else:
                # If no project, use global counter
                new_number = 1
            
            self.lem_number = f"LEM-{new_number:03d}"
        super().save(*args, **kwargs)

    @property
    def display_number(self):
        """Bare number without the FT-/FTF-/CT- prefix, plus "-R01" style suffix once revised."""
        return format_display_number(self.lem_number, self.revision)

    def __str__(self):
        return self.lem_number


def format_display_number(lem_number, revision=0):
    base = (lem_number or "").split("-", 1)[-1]
    return f"{base}-R{revision:02d}" if revision else base


class LEMRevision(models.Model):
    """One issued version of a field ticket. Created the first time a ticket is revised (revision 0 = the
    original, captured from the ticket as it stood) and on every revision after that, so every issued PDF can
    still be downloaded exactly as it was."""

    lem = models.ForeignKey(LEMReport, on_delete=models.CASCADE, related_name="revisions")
    revision = models.PositiveIntegerField()
    report_data = models.JSONField(default=dict)
    time_entry_ids = models.JSONField(default=list)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["lem", "revision"]]
        ordering = ["revision"]

    def __str__(self):
        return f"{self.lem.lem_number} r{self.revision}"


class DailyTicketDownload(models.Model):
    """One blank paper LEM handed out for filling in by hand in the field. Every download reserves the
    next ticket number, so each printed ticket is unique and can be traced back to who downloaded it and
    when."""

    DAILY_TICKET = "daily_ticket"
    COST_SHEET = "cost_sheet"
    TEMPLATE_CHOICES = (
        (DAILY_TICKET, "Daily Ticket (Template 1)"),
        (COST_SHEET, "Daily Cost Sheet (Template 2)"),
    )

    # Each template has its own number series. The printed number is "<yy>-<sequence>": the counter
    # restarts on January 1 (Alberta time), so the first ticket of 2027 is "27-00001".
    # (template, year, sequence) is unique, so numbers are never reused within a series.
    template = models.CharField(max_length=20, choices=TEMPLATE_CHOICES, default=DAILY_TICKET)
    year = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    ticket_number = models.CharField(max_length=20)
    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    downloaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="daily_ticket_downloads",
    )
    downloaded_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-sequence"]
        unique_together = [("template", "year", "sequence")]

    def __str__(self):
        return self.ticket_number

    @classmethod
    def issue(cls, user, template=DAILY_TICKET, workspace=None, ip_address=None, on_issued=None):
        """Reserve the next ticket number in this template's series for the current year. The unique
        (template, year, sequence) constraint settles races between concurrent downloads; the loser
        simply retries with the following number.

        `on_issued(ticket)` runs inside the same transaction (e.g. building the PDF), so if it fails the
        number is not used up. Returns (ticket, on_issued's result)."""
        start = getattr(settings, "DAILY_TICKET_START_NUMBER", 1)
        for _ in range(5):
            try:
                with transaction.atomic():
                    now = timezone.now()
                    year = now.astimezone(ENVISION_TZ).year
                    last = (
                        cls.objects.select_for_update()
                        .filter(template=template, year=year)
                        .only("sequence")
                        .order_by("-sequence")
                        .first()
                    )
                    sequence = max(last.sequence + 1 if last else start, start)
                    ticket = cls.objects.create(
                        template=template,
                        year=year,
                        sequence=sequence,
                        ticket_number=f"{year % 100:02d}-{sequence:05d}",
                        workspace=workspace,
                        downloaded_by=user,
                        downloaded_at=now,
                        ip_address=ip_address,
                    )
                    return ticket, (on_issued(ticket) if on_issued else None)
            except IntegrityError:
                continue
        raise IntegrityError("Could not reserve a Daily Ticket number, please try again.")
