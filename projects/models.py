from django.db import models
from core.models import SoftDeleteModel
from workspaces.models import Workspace
from clients.models import Client
from users.models import User
import uuid

class Project(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, null=True, blank=True)
    default_rt_hours = models.DecimalField(max_digits=5, decimal_places=2, default=8, null=True, blank=True)
    client_hours_rate = models.DecimalField(max_digits=8, decimal_places=4,null=True, blank=True)
    ot_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=2)  # default double rate
    is_active = models.BooleanField(default=True)
    billable = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_projects')
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.name


class JobTitle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class ProjectRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_roles")
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ("project", "job_title")

    def __str__(self):
        return f"{self.project.name} - {self.job_title.name}"

class UserProjectRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="project_roles")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="user_roles")
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)  # optional: override project rate

    class Meta:
        unique_together = ("user", "project", "job_title")

    def __str__(self):
        return f"{self.user.email} → {self.job_title.name} @ {self.project.name}"

