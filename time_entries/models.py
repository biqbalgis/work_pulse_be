from django.db import models
from core.models import SoftDeleteModel
from workspaces.models import Workspace
from projects.models import Project, JobTitle
from tasks.models import Task
from users.models import User
import uuid

class TimeEntry(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True)
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE, null=True)  # NEW
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(default=0)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True)  # NEW
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # NEW
    billable = models.BooleanField(default=False)
    meals = models.BooleanField(default=False)
    hotels = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_timeentry')

    class Meta:
        indexes = [
            # Every report/dashboard query filters by workspace + a start_time
            # range; without this it's a full table scan once TimeEntry grows.
            models.Index(fields=['workspace', 'start_time'], name='timeentry_workspace_start_idx'),
            models.Index(fields=['user', 'start_time'], name='timeentry_user_start_idx'),
        ]

    def save(self, *args, **kwargs):
        # Time on a non-billable project is never billable to the client.
        if self.project_id and self.billable and not self.project.billable:
            self.billable = False
        super().save(*args, **kwargs)

    def field_ticket(self):
        """The (live) field ticket this time is on, or None."""
        tickets = [lem for lem in self.lem_reports.all() if not lem.is_deleted]
        return tickets[0] if tickets else None

    def timesheet_lock_reason(self):
        """Why this entry can't be changed from the timesheet, or None. Time on a field ticket is changed by an
        admin revising the ticket; once the ticket is invoiced, not at all."""
        ticket = self.field_ticket()
        if ticket is None:
            return None
        if ticket.status == "invoiced":
            return f"This time is on field ticket {ticket.ticket_number}, which is invoiced, so it can't be changed."
        return f"This time is on field ticket {ticket.ticket_number}. To change it, an admin revises the field ticket."

    @property
    def is_billable_to_client(self):
        """Counts toward client costs and invoices: the entry and its project are both billable."""
        return bool(self.billable and (self.project is None or self.project.billable))

    def __str__(self):
        return f"{self.user} - {self.project}"
