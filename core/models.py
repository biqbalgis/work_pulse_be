from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    class Meta:
        abstract = True

class ActivityLog(models.Model):
    ACTIONS = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
    ]
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    model_name = models.CharField(max_length=100)
    object_id = models.IntegerField(null=True, blank=True)
    extra_data = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} {self.action} {self.model_name} at {self.timestamp}"
