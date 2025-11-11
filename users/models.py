from django.contrib.auth.models import AbstractUser
from django.db import models
from core.models import SoftDeleteModel
import uuid

class User(AbstractUser, SoftDeleteModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    def __str__(self):
        return self.username
