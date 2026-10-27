from django.apps import AppConfig


class TimeEntriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "time_entries"

    def ready(self):
        from . import signals  # noqa: F401  (connects the repricing signals)
