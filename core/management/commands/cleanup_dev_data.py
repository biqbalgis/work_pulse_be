from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from workspaces.models import Workspace, WorkspaceMember
from clients.models import Client
from projects.models import Project, JobTitle, ProjectRole, UserProjectRole
from tasks.models import Task
from time_entries.models import TimeEntry
from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from organization_asset.models import OrganizationAsset, AssetUsage

User = get_user_model()

class Command(BaseCommand):
    help = "Cleans all seeded development data."

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("⚠ Cleaning development seed data..."))

        # Delete related tables in proper order
        AssetUsage.objects.all().delete()
        TimeEntryApprovalItem.objects.all().delete()
        TimeEntryApproval.objects.all().delete()
        TimeEntry.objects.all().delete()

        OrganizationAsset.objects.all().delete()

        UserProjectRole.objects.all().delete()
        ProjectRole.objects.all().delete()
        Project.objects.all().delete()
        Client.objects.all().delete()

        WorkspaceMember.objects.all().delete()
        Workspace.objects.all().delete()

        # Keep one superuser
        User.objects.exclude(is_superuser=True).delete()

        # Keep Tasks & Job Titles (optional to remove)
        Task.objects.all().delete()
        JobTitle.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("🧹 Database cleaned. You can now reseed."))

