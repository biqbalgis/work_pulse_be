"""
Reprice existing time by the pay rules (core.utils.pay_rules): each entry's labour amount and its hourly crew
rates get the overtime (1.5x) / double-time (2x) premium for where they fall in the person's day. New and edited
time is priced automatically; run this once after deploying the pay rules so older time matches.

    python manage.py reprice_time                     # everything
    python manage.py reprice_time --since 2026-01-01  # time from that day on

Entries on an invoiced field ticket keep what was invoiced. Issued field ticket PDFs aren't regenerated.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.utils.pay_rules import entry_day, reprice_days
from time_entries.models import TimeEntry


class Command(BaseCommand):
    help = "Reprice existing time entries by the overtime / double-time pay rules."

    def add_arguments(self, parser):
        parser.add_argument("--since", help="Only time on or after this date (YYYY-MM-DD).")

    def handle(self, *args, **opts):
        entries = TimeEntry.objects.filter(is_deleted=False).only("id", "user_id", "start_time")
        if opts["since"]:
            try:
                since = date.fromisoformat(opts["since"])
            except ValueError as exc:
                raise CommandError("--since must be YYYY-MM-DD") from exc
            entries = entries.filter(start_time__date__gte=since)
        days = sorted({(e.user_id, entry_day(e)) for e in entries.iterator()}, key=lambda d: (str(d[0]), d[1]))
        batch = 200
        for start in range(0, len(days), batch):
            reprice_days(days[start:start + batch])
            self.stdout.write(f"  {min(start + batch, len(days))}/{len(days)} person-days repriced")
        self.stdout.write(self.style.SUCCESS(f"Repriced {len(days)} person-days."))
