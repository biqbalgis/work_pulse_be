from django.apps import AppConfig


class UserPermissionsConfig(AppConfig):
    name = "user_permissions"

    def ready(self):
        from . import signals  # noqa: F401
