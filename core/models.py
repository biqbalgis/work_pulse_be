from django.db import models, transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def get_or_restore(self, defaults=None, update_existing=None, **lookup):
        """
        Soft-delete–aware replacement for get_or_create / update_or_create.

        Behaviour (in order):
          1. Live record found (is_deleted=False):
               - Apply `update_existing` fields if provided and save.
               - Return (obj, False).

          2. Soft-deleted record found (is_deleted=True):
               - Restore it (is_deleted=False, deleted_at=None).
               - Apply `defaults` on top of the restored values.
               - Return (obj, True)  — caller sees it as "created".

          3. No record at all:
               - Create with lookup + defaults inside a savepoint.
               - If a concurrent request causes IntegrityError, fetch and
                 return the row that won the race (obj, False).

        Args:
            lookup        : field=value pairs used to identify the record
                            (same as the non-defaults kwargs in get_or_create).
            defaults      : dict applied during creation AND restoration.
            update_existing: dict applied only when a live record already exists
                            (useful for update_or_create style calls).

        Returns:
            (instance, created_or_restored: bool)
        """
        defaults = defaults or {}
        update_existing = update_existing or {}

        # ── 1. Live record ────────────────────────────────────────────────────
        obj = self.filter(**lookup).first()
        if obj is not None:
            if update_existing:
                for field, value in update_existing.items():
                    setattr(obj, field, value)
                obj.save(update_fields=list(update_existing.keys()))
            return obj, False

        # ── 2. Soft-deleted record ────────────────────────────────────────────
        soft_deleted = self.model.all_objects.filter(**lookup, is_deleted=True).first()
        if soft_deleted is not None:
            restore = {"is_deleted": False, "deleted_at": None, **defaults, **update_existing}
            for field, value in restore.items():
                setattr(soft_deleted, field, value)
            soft_deleted.save(update_fields=list(restore.keys()))
            return soft_deleted, True

        # ── 3. Create fresh (savepoint guards against race conditions) ────────
        try:
            with transaction.atomic():
                obj = self.model(**lookup, **defaults)
                obj.save(force_insert=True)
                return obj, True
        except IntegrityError:
            # A concurrent request created the row just before us.
            # The savepoint rolled back; fetch the winner.
            obj = self.filter(**lookup).first()
            if obj is not None:
                return obj, False
            raise  # genuine unexpected error — re-raise


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    class Meta:
        abstract = True

class ActivityLog(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=50, null=True, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.timestamp}] {self.user} - {self.action} - {self.model_name}"


class ErrorLog(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)
    message = models.TextField()
    traceback = models.TextField()
    path = models.CharField(max_length=500, null=True, blank=True)
    method = models.CharField(max_length=10, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.timestamp}] {self.message[:80]}"
