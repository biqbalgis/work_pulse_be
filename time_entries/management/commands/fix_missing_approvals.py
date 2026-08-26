"""
Management command — backfill missing TimeEntryApproval / TimeEntryApprovalItem
records for any TimeEntry that was saved without going through the approval flow.

Usage:
    python manage.py fix_missing_approvals
    python manage.py fix_missing_approvals --dry-run
    python manage.py fix_missing_approvals --user <uuid>
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from approvals.utils import get_week_bounds
from time_entries.models import TimeEntry
from workspaces.models import WorkspaceMember


class Command(BaseCommand):
    help = "Backfill missing approval records for time entries that have none."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be fixed without writing anything to the DB.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Limit the fix to a single user UUID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user_filter = options["user"]

        # All time entries that have NO TimeEntryApprovalItem pointing at them
        orphaned_qs = (
            TimeEntry.objects
            .filter(is_deleted=False)
            .exclude(
                id__in=TimeEntryApprovalItem.objects
                       .values_list("time_entry_id", flat=True)
            )
            .select_related("user", "workspace", "project")
            .order_by("user_id", "start_time")
        )

        if user_filter:
            orphaned_qs = orphaned_qs.filter(user_id=user_filter)

        total = orphaned_qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned time entries found. Nothing to fix."))
            return

        self.stdout.write(f"Found {total} time entr{'y' if total == 1 else 'ies'} with no approval record.")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written."))

        fixed = 0
        skipped = 0

        for entry in orphaned_qs:
            user      = entry.user
            workspace = entry.workspace

            # Fallback: look up workspace from membership if not on the entry
            if not workspace:
                wm = WorkspaceMember.objects.filter(user=user).first()
                if not wm:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  SKIP  entry {entry.id} — user {user} has no workspace membership."
                        )
                    )
                    skipped += 1
                    continue
                workspace = wm.workspace

            entry_date          = entry.start_time.date()
            start_week, end_week = get_week_bounds(entry_date)

            if dry_run:
                self.stdout.write(
                    f"  WOULD FIX  entry {entry.id} | user: {user.get_full_name()} "
                    f"| date: {entry_date} | week: {start_week} → {end_week}"
                )
                fixed += 1
                continue

            try:
                with transaction.atomic():
                    approval, created = TimeEntryApproval.objects.get_or_create(
                        workspace=workspace,
                        user=user,
                        start_date=start_week,
                        end_date=end_week,
                        defaults={"status": "submitted", "created_by": user},
                    )

                    TimeEntryApprovalItem.objects.create(
                        approval=approval,
                        time_entry=entry,
                        approved=True,
                        created_by=user,
                    )

                    # Recalculate total_hours
                    total_minutes = (
                        TimeEntryApprovalItem.objects
                        .filter(approval=approval, time_entry__is_deleted=False)
                        .aggregate(total=Sum("time_entry__duration"))["total"] or 0
                    )
                    approval.total_hours = Decimal(total_minutes) / Decimal(60)
                    approval.save(update_fields=["total_hours"])

                label = "NEW approval" if created else "EXISTING approval"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  FIXED  entry {entry.id} | user: {user.get_full_name()} "
                        f"| date: {entry_date} | {label} {approval.id}"
                    )
                )
                fixed += 1

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  ERROR  entry {entry.id} — {exc}")
                )
                skipped += 1

        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN complete. Would fix {fixed}, skip {skipped}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. Fixed: {fixed}  |  Skipped: {skipped}"))
