import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from projects.models import Project
from reports.models import LEMReport
from reports.pdf_utils import generate_daily_lem_pdf
from time_entries.models import TimeEntry
from users.models import User
from workspaces.models import Workspace, WorkspaceMember


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_starts(start, end):
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        yield date(year, month, 1)
        month += 1
        if month == 13:
            year, month = year + 1, 1


def month_bounds(month_start, start, end):
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)
    last_day = date.fromordinal(next_month.toordinal() - 1)
    return max(month_start, start), min(last_day, end)


def safe_name(value):
    return re.sub(r'[\\/:*?"<>|]+', "_", value).strip() or "project"


class Command(BaseCommand):
    help = (
        "One-time job: for every project in a workspace, create (or reuse) the Daily LEM "
        "for each day that has time entries, and save each month's PDFs into its own folder."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", default="stamsh", help="Workspace name (case-insensitive contains match).")
        parser.add_argument("--start", default="2026-05-01", help="YYYY-MM-DD")
        parser.add_argument("--end", default="2026-09-30", help="YYYY-MM-DD")
        parser.add_argument("--out-dir", default="lem_downloads", help="Folder the monthly sub-folders are written to.")
        parser.add_argument("--requester-email", help="User recorded as requester on newly created LEMs (default: a workspace admin).")
        parser.add_argument("--include-empty", action="store_true", help="Also create a LEM for days with no time entries (burns LEM numbers).")
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be created; no LEMs or files are written.")

    def handle(self, *args, **opts):
        start, end = parse_date(opts["start"]), parse_date(opts["end"])
        if end < start:
            raise CommandError("--end must be on or after --start.")

        workspaces = Workspace.objects.filter(name__icontains=opts["workspace"])
        if workspaces.count() != 1:
            names = ", ".join(w.name for w in workspaces) or "none"
            raise CommandError(f"Expected exactly one workspace matching '{opts['workspace']}', found: {names}")
        workspace = workspaces.first()

        requester = self._resolve_requester(workspace, opts["requester_email"])
        projects = list(Project.objects.filter(workspace=workspace).order_by("name"))

        self.stdout.write(f"Workspace: {workspace.name}")
        self.stdout.write(f"Projects:  {len(projects)}")
        self.stdout.write(f"Requester: {requester.email if requester else '-'}")
        self.stdout.write(f"Range:     {start} -> {end}")
        self.stdout.write(self.style.WARNING("DRY RUN - nothing will be written.") if opts["dry_run"] else "")

        out_dir = Path(opts["out_dir"])
        if not opts["dry_run"]:
            out_dir.mkdir(parents=True, exist_ok=True)

        grand_total, grand_failed = 0, 0

        for month_start in month_starts(start, end):
            m_start, m_end = month_bounds(month_start, start, end)
            label = month_start.strftime("%Y-%m")
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {label} ({m_start} -> {m_end}) ==="))

            jobs = []
            for project in projects:
                for day in self._days_for(project, m_start, m_end, opts["include_empty"]):
                    jobs.append((project, day))

            if opts["dry_run"]:
                self.stdout.write(f"  {len(jobs)} daily LEM PDF(s) would be created")
                grand_total += len(jobs)
                continue

            if not jobs:
                self.stdout.write("  nothing to do")
                continue

            month_dir = out_dir / label
            month_dir.mkdir(parents=True, exist_ok=True)
            done = failed = 0
            for project, day in jobs:
                try:
                    with transaction.atomic():
                        lem_number, pdf_bytes = self._build_daily_lem(project, day, requester)
                    pdf_path = month_dir / f"{day.isoformat()}_{safe_name(project.name)}_{lem_number}.pdf"
                    pdf_path.write_bytes(pdf_bytes)
                    done += 1
                except Exception as exc:
                    failed += 1
                    self.stderr.write(self.style.ERROR(f"  FAILED {project.name} {day}: {exc}"))

            self.stdout.write(self.style.SUCCESS(f"  {done} PDF(s) -> {month_dir}") + (f"  ({failed} failed)" if failed else ""))
            grand_total += done
            grand_failed += failed

        self.stdout.write(self.style.SUCCESS(f"\nTotal: {grand_total} PDF(s)") + (f", {grand_failed} failed" if grand_failed else ""))

    def _resolve_requester(self, workspace, email):
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                raise CommandError(f"No user with email {email}")
            return user
        member = (
            WorkspaceMember.objects.filter(workspace=workspace, role="admin")
            .select_related("user")
            .order_by("joined_at")
            .first()
        )
        return member.user if member else None

    def _days_for(self, project, m_start, m_end, include_empty):
        if include_empty:
            return [date.fromordinal(o) for o in range(m_start.toordinal(), m_end.toordinal() + 1)]

        starts = (
            TimeEntry.objects.filter(
                project=project,
                start_time__date__gte=m_start,
                start_time__date__lte=m_end,
            ).values_list("start_time", flat=True)
        )
        days = {timezone.localtime(s).date() for s in starts}
        return sorted(days)

    def _build_daily_lem(self, project, report_date, requester):
        entries = (
            TimeEntry.objects.filter(project=project, start_time__date=report_date)
            .select_related("user", "job_title")
            .prefetch_related("asset_usages__asset")
        )

        rows = []
        for entry in entries:
            rows.append({
                "name": f"{entry.user.first_name} {entry.user.last_name}",
                "role": entry.job_title.name if entry.job_title else "",
                "start": entry.start_time.strftime("%H:%M"),
                "end": entry.end_time.strftime("%H:%M") if entry.end_time else "",
                "hours": round(entry.duration / 60, 2),
                "extras": ", ".join(u.asset.name for u in entry.asset_usages.all()),
            })

        lem_report = LEMReport.objects.filter(
            project=project, lem_date=report_date, lem_number__startswith="LEM-"
        ).first()
        if not lem_report:
            lem_report = LEMReport.objects.create(
                requester=requester, project=project, lem_date=report_date
            )

        result = {
            "project_name": project.name,
            "client_name": project.client.name if project.client else "",
            "date": report_date.isoformat(),
            "lem_number": lem_report.lem_number,
            "sign": None,
            "sign_name": "",
            "sign_date": "",
            "rows": rows,
        }
        lem_report.report_data = result
        lem_report.save()
        lem_report.time_entries.set(entries)

        return lem_report.lem_number, generate_daily_lem_pdf(result).getvalue()
