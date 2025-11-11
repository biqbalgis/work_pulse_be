from django.db import models
from workspaces.models import Workspace
from time_entries.models import TimeEntry

class Tag(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True, null=True)

class TimeEntryTag(models.Model):
    time_entry = models.ForeignKey(TimeEntry, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
