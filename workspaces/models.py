
from django.db import models
from core.models import SoftDeleteModel
from users.models import User
from work_pulse_be import settings
import uuid


def workspace_logo_upload_path(instance, filename):
    return f"workspace_logos/{instance.id}/{filename}"


class Workspace(SoftDeleteModel):
    OVERTIME_POLICY_CHOICES = (
        ('standard', 'Standard (per-project daily RT/OT table, e.g. Stamsh)'),
        ('envision', 'EnvisionGeo (8h/day every day, 44h weekly cap Sun-Sat, OT @ 1.5x)'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, null=True)
    logo = models.ImageField(upload_to=workspace_logo_upload_path, null=True, blank=True)
    overtime_policy = models.CharField(
        max_length=20,
        choices=OVERTIME_POLICY_CHOICES,
        null=True,
        blank=True,
        default=None,
        help_text='Optional. Leave empty for no overtime policy (all hours counted as regular).'
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_workspaces')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']  # 👈 Fix: always order newest first

    def __str__(self):
        return self.name

    @property
    def is_envision(self):
        """Envision workspaces ("Envision", "Envision Geo", ...) use the predefined user groups."""
        return (self.name or "").strip().lower().startswith("envision")


class WorkspaceMember(SoftDeleteModel):
    # Predefined user groups; a member belongs to at most one. Used to filter / split Custom Reports.
    GROUP_CHOICES = (
        ('subcontractors', 'Subcontractors'),
        ('external_envision', 'External Envision'),
        ('internal_envision', 'Internal Envision'),
    )

    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('field_manager', 'Field Manager'),
        ('user', 'User'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    group = models.CharField(max_length=30, choices=GROUP_CHOICES, null=True, blank=True, db_index=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    manager = models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=True,related_name="team_members")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_workspacesmembers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('workspace', 'user')

    def __str__(self):
        return f"{self.user} - {self.workspace}"

class Holiday(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='holidays', null=True, blank=True)
    name = models.CharField(max_length=255)
    date = models.DateField()
    
    class Meta:
        ordering = ['date']
        unique_together = ('workspace', 'date')

    def __str__(self):
        return f"{self.name} ({self.date})"
