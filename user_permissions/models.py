import uuid
from django.db import models
from users.models import User
from workspaces.models import Workspace


class UserPermission(models.Model):
    """
    Stores which sections of the application a user is allowed to see
    within a specific workspace.

    Superusers and admins always have full access regardless of this table.
    For all other roles the frontend should consult these flags before
    rendering navigation items or allowing access to a section.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="app_permissions")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="user_permissions")

    # ── Section visibility flags ───────────────────────────────────────────
    dashboard         = models.BooleanField(default=False)
    reports           = models.BooleanField(default=False)
    timesheet         = models.BooleanField(default=False)
    admin_timesheet   = models.BooleanField(default=False)
    field_ticket      = models.BooleanField(default=False)
    time_off          = models.BooleanField(default=False)
    approval          = models.BooleanField(default=False)
    assets            = models.BooleanField(default=False)
    projects          = models.BooleanField(default=False)
    clients           = models.BooleanField(default=False)
    organization      = models.BooleanField(default=False)
    team              = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "workspace")

    def __str__(self):
        return f"{self.user} — {self.workspace}"
