
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()

class LEMReport(models.Model):
    lem_number = models.CharField(max_length=20, editable=False)
    requester  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    project    = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True)
    task       = models.ForeignKey("tasks.Task", on_delete=models.SET_NULL, null=True, blank=True)
    lem_date   = models.DateField(null=True, blank=True)
    report_data = models.JSONField(default=dict)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['project', 'lem_number']]

    def save(self, *args, **kwargs):
        if not self.lem_number:
            # Generate sequential number per project
            if self.project:
                last_entry = LEMReport.objects.filter(
                    project=self.project
                ).order_by("-id").first()
                
                if last_entry and last_entry.lem_number.startswith("LEM-"):
                    try:
                        # Extract number from LEM-001 format
                        last_number = int(last_entry.lem_number.split('-')[1])
                        new_number = last_number + 1
                    except (ValueError, IndexError):
                        new_number = 1
                else:
                    new_number = 1
            else:
                # If no project, use global counter
                new_number = 1
            
            self.lem_number = f"LEM-{new_number:03d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.lem_number
