"""
Delete old audit-log rows so the table (one row per write request) doesn't grow forever.

    python manage.py prune_activity_logs                 # dry run: counts rows older than 180 days
    python manage.py prune_activity_logs --days 365      # different retention
    python manage.py prune_activity_logs --apply         # actually delete

Deletes in small batches (default 5000 rows) so it never holds a long lock on a busy table, and can be run
from cron / a scheduled job. Only ActivityLog is touched; ErrorLog is left alone.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ActivityLog


class Command(BaseCommand):
    help = "Delete ActivityLog rows older than --days (default 180). Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=180, help="Keep this many days of history (default 180).")
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--apply", action="store_true", help="Delete the rows (default is a dry run).")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            self.stderr.write("--days must be at least 1")
            return
        cutoff = timezone.now() - timedelta(days=days)
        stale = ActivityLog.objects.filter(timestamp__lt=cutoff)
        total = stale.count()
        self.stdout.write(f"ActivityLog rows older than {days} days (before {cutoff:%Y-%m-%d}): {total}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run. Re-run with --apply to delete them."))
            return

        deleted = 0
        batch_size = options["batch_size"]
        while True:
            ids = list(stale.values_list("pk", flat=True)[:batch_size])
            if not ids:
                break
            ActivityLog.objects.filter(pk__in=ids)._raw_delete(using=ActivityLog.objects.db)
            deleted += len(ids)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} rows."))
