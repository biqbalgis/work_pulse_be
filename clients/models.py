from django.db import models
from core.models import SoftDeleteModel
from workspaces.models import Workspace

class Client(SoftDeleteModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    contact_info = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
