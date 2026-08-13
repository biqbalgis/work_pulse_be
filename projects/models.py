
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
    job_code = models.CharField(max_length=20, blank=True, null=True)
    color = models.CharField(max_length=7, null=True, blank=True)
    default_rt_hours = models.DecimalField(max_digits=5, decimal_places=2, default=8, null=True, blank=True)
    client_hours_rate = models.DecimalField(max_digits=8, decimal_places=4,null=True, blank=True)
    ot_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=2)  # default double rate
    pm_info = models.JSONField(default=dict, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    billable = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_projects')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = [('workspace', 'job_code')]
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # Auto-assign job_code only on creation if not provided
        if not self.pk and not self.job_code:
            from .utils import get_next_job_code
            self.job_code = get_next_job_code(self.workspace)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class JobTitle(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="job_titles")
    name = models.CharField(max_length=255)

    class Meta:
        unique_together = ("workspace", "name")

    def __str__(self):
        return self.name


class ProjectRole(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="project_roles")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_roles")
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ("project", "job_title")

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.project_id:
            self.workspace_id = self.project.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.project.name} - {self.job_title.name}"

class UserProjectRole(SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="user_project_roles")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="project_roles")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="user_roles")
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)  # optional: override project rate

    class Meta:
        unique_together = ("user", "project", "job_title")

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.project_id:
            self.workspace_id = self.project.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.email} → {self.job_title.name} @ {self.project.name}"


class RoleTemplate(SoftDeleteModel):
    """
    A named template that groups multiple users under a single job title.
    Used to quickly re-apply the same crew+role combination across projects.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template_name = models.CharField(max_length=255)
    job_title   = models.ForeignKey(JobTitle, on_delete=models.CASCADE, related_name='role_templates')
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_role_templates')
    updated_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_role_templates')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.template_name} ({self.job_title.name})"


class RoleTemplateUser(SoftDeleteModel):
    """Each row links one user to a RoleTemplate."""
    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(RoleTemplate, on_delete=models.CASCADE, related_name='template_users')
    user     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='role_template_entries')

    class Meta:
        unique_together = ('template', 'user')

    def __str__(self):
        return f"{self.template.template_name} → {self.user}"


class LaborRate(SoftDeleteModel):
    ROLE_CONDITIONS = [
        ('Day', 'Day'),
        ('Night', 'Night'),
        ('Saturday', 'Saturday'),
        ('Saturday Night', 'Saturday Night'),
        ('Sunday Day', 'Sunday Day'),
        ('Sunday Night', 'Sunday Night'),
        ('Holiday', 'Holiday'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE, related_name='labor_rates')
    condition = models.CharField(max_length=50, choices=ROLE_CONDITIONS)
    
    regular_cost = models.DecimalField(max_digits=10, decimal_places=2)
    overtime_cost = models.DecimalField(max_digits=10, decimal_places=2)
    double_time_cost = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('job_title', 'condition')

    def __str__(self):
        return f"{self.job_title.name} - {self.condition}"
