"""
One-time cleanup: remove passwords/tokens that were already written to the audit and error logs.

Before the logging fix, ActivityLog.extra_data["body"] held the raw request body for credential endpoints
(change-password stored old_password / new_password in clear text; unparseable bodies were stored as raw bytes),
and ErrorLog.extra_data["body"] held the raw body of every request that raised an unhandled exception.

    python manage.py redact_logged_passwords            # dry run: only counts what would change
    python manage.py redact_logged_passwords --apply    # actually rewrites those rows

Only rows that contain something to redact are touched, in small batches. Nothing else is modified.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import ActivityLog, ErrorLog
from core.utils.redaction import redact_sensitive_data

# Endpoints whose request bodies carry credentials.
CREDENTIAL_PATHS = [
    "/api/login/",
    "/api/token/",
    "/api/token/refresh/",
    "/api/register/",
    "/api/users/",
    "/api/auth/change-password/",
    "/api/auth/reset-password/",
    "/api/auth/forgot-password/",
]


def _clean_body(body):
    """Return (new_body, changed)."""
    if isinstance(body, str):
        # The old raw-body fallback: str(bytes) — drop it, keep only that it existed.
        return {"unlogged": True, "reason": "raw body removed (may have contained credentials)"}, True
    if isinstance(body, (dict, list)):
        cleaned = redact_sensitive_data(body)
        return cleaned, cleaned != body
    return body, False


class Command(BaseCommand):
    help = "Redact passwords/tokens already stored in ActivityLog and ErrorLog (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run).")
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        apply = options["apply"]
        batch_size = options["batch_size"]
        totals = {}

        activity_qs = ActivityLog.objects.filter(
            Q(extra_data__path__in=CREDENTIAL_PATHS)
            | Q(extra_data__body__startswith="b'")
            | Q(extra_data__body__startswith='b"')
        )
        totals["ActivityLog"] = self._process(activity_qs, apply, batch_size)
        totals["ErrorLog"] = self._process(ErrorLog.objects.all(), apply, batch_size)

        verb = "Redacted" if apply else "Would redact"
        for name, (scanned, changed) in totals.items():
            self.stdout.write(f"{name}: scanned {scanned}, {verb.lower()} {changed}")
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run. Re-run with --apply to rewrite these rows."))

    def _process(self, queryset, apply, batch_size):
        scanned = changed = 0
        pending = []
        for row in queryset.order_by("pk").iterator(chunk_size=batch_size):
            scanned += 1
            data = row.extra_data if isinstance(row.extra_data, dict) else {}
            if "body" not in data:
                continue
            new_body, did_change = _clean_body(data["body"])
            if not did_change:
                continue
            changed += 1
            if apply:
                row.extra_data = {**data, "body": new_body}
                pending.append(row)
                if len(pending) >= batch_size:
                    type(row).objects.bulk_update(pending, ["extra_data"])
                    pending = []
        if apply and pending:
            type(pending[0]).objects.bulk_update(pending, ["extra_data"])
        return scanned, changed
