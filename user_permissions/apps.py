from django.apps import AppConfig


class UserPermissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "user_permissions"

    def ready(self):
        from . import signals  # noqa: F401  (gives every new workspace member their access)
