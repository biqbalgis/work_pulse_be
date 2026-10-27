import uuid

from django.db import models
from django.utils import timezone

from users.models import User
from workspaces.models import Workspace


class Notification(models.Model):
    """One in-app notification (the bell): something happened to the recipient's time or field ticket."""

    TIME_APPROVED = "time_approved"
    TIME_REJECTED = "time_rejected"
    TICKET_APPROVED = "ticket_approved"
    TICKET_REJECTED = "ticket_rejected"
    TICKET_INVOICED = "ticket_invoiced"
    TICKET_UPDATED = "ticket_updated"
    KIND_CHOICES = (
        (TIME_APPROVED, "Time approved"),
        (TIME_REJECTED, "Time rejected"),
        (TICKET_APPROVED, "Field ticket approved"),
        (TICKET_REJECTED, "Field ticket rejected"),
        (TICKET_INVOICED, "Field ticket invoiced"),
        (TICKET_UPDATED, "Field ticket updated"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    # Where clicking it goes in the app, e.g. "/timesheet?week=2026-10-18" or "/field-tickets?ticket=26-000062".
    link = models.CharField(max_length=300, blank=True, default="")
    # What it's about: "time_entry", "approval" (a week) or "field_ticket", and that object's id.
    target_type = models.CharField(max_length=30, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"], name="notification_unread_idx")]

    def __str__(self):
        return f"{self.recipient} · {self.title}"
