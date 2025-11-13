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
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='created_timeentry'
    )

    def __str__(self):
        return f"{self.user} - {self.project}"
