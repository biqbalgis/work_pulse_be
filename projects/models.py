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
    is_active = models.BooleanField(default=True)
    billable = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_projects')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
