import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.models import AbstractUser, User
from django.db import models
from django.utils import timezone

from core.models import SoftDeleteModel

class User(AbstractUser, SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    def __str__(self):
        return self.username


class PasswordResetToken(models.Model):
    """
    A single-use, expiring token for the forgot-password flow. Deliberately a
    plain opaque token (not Django's uidb64+token pair) so the reset link and
    API only ever need to carry one value.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PasswordResetToken(user={self.user_id}, used={bool(self.used_at)})"

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    @classmethod
    def issue_for_user(cls, user, ttl_minutes=60):
        # Invalidate any previous unused link so only the newest email works.
        cls.objects.filter(user=user, used_at__isnull=True).delete()
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )
