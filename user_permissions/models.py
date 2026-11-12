import uuid
from django.db import models
from users.models import User
from workspaces.models import Workspace


class PermissionGroup(models.Model):
    """A set of permissions (see user_permissions.catalog) given to everyone in the group. Each workspace has its own
    groups; each person is in one group per workspace (UserPermission.group). The built-in Administrator group
    (is_system) always has every permission and can't be edited or removed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="permission_groups")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True, default="")
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False)
    # New members are put here unless someone picks another group.
    is_default = models.BooleanField(default=False)
    # Until every API reads permissions (phase 2), the old role a member of this group gets for the checks that
    # still go by role. The Administrator group gives "admin".
    base_role = models.CharField(max_length=20, default="user")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("workspace", "name")
        ordering = ["-is_system", "name"]

    def __str__(self):
        return f"{self.name} ({self.workspace})"


class UserPermission(models.Model):
    """
    A person's access in one workspace: their permission group, plus permissions granted to or removed from them
    personally (user_permissions.access works out the result).

    The yes/no section flags below are the old way of doing this. They're kept, unused, until the move to groups
    has been checked everywhere, and /me/ still returns them (worked out from the new permissions) for the menu.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="app_permissions")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="user_permissions")

    # ── Section visibility flags ───────────────────────────────────────────
    dashboard         = models.BooleanField(default=False)
    reports           = models.BooleanField(default=False)
    custom_report     = models.BooleanField(default=False)
    timesheet        = models.BooleanField(default=False)
    admin_timesheet   = models.BooleanField(default=False)
    field_ticket      = models.BooleanField(default=False)
    time_off          = models.BooleanField(default=False)
    approval          = models.BooleanField(default=False)
    assets            = models.BooleanField(default=False)
    projects          = models.BooleanField(default=False)
    clients           = models.BooleanField(default=False)
    organization      = models.BooleanField(default=False)
    team              = models.BooleanField(default=False)
    # Not a section: allows marking field tickets (LEMs) Approved / Rejected.
    lem_approval      = models.BooleanField(default=False)

    group = models.ForeignKey(PermissionGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    # Permission codes added to / taken away from this one person on top of their group.
    granted = models.JSONField(default=list, blank=True)
    revoked = models.JSONField(default=list, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "workspace")

    def __str__(self):
        return f"{self.user} — {self.workspace}"
