from django.contrib.auth.models import AbstractUser
from django.db import models
from core.models import SoftDeleteModel

class User(AbstractUser, SoftDeleteModel):
    email = models.EmailField(unique=True)
    def __str__(self):
        return self.username
