
from django.db import models
from core.models import SoftDeleteModel
from users.models import User
from work_pulse_be import settings
import uuid

class Workspace(SoftDeleteModel):
    OVERTIME_POLICY_CHOICES = (
        ('standard', 'Standard (per-project daily RT/OT table, e.g. Stamsh)'),
        ('envision', 'EnvisionGeo (8h/day every day, 44h weekly cap Sun-Sat, OT @ 1.5x)'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, null=True)
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

class WorkspaceMember(SoftDeleteModel):
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
