from django.db import models
from users.models import User
from workspaces.models import Workspace

class TimeOffType(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    requires_approval = models.BooleanField(default=True)
    color = models.CharField(max_length=7, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class TimeOffRequest(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    type = models.ForeignKey(TimeOffType, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='pending')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_timeoffs')
    reviewer_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
