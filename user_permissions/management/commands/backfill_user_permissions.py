from django.core.management.base import BaseCommand

from user_permissions.models import DEFAULT_NEW_MEMBER_PERMISSIONS, UserPermission
from workspaces.models import WorkspaceMember


class Command(BaseCommand):
    help = (
        "Create the default permission row for every workspace member who does not have one yet. "
        "Members that already have a row are never changed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", help="Only members of workspaces whose name contains this text.")
        parser.add_argument("--dry-run", action="store_true", help="List who would get a row; write nothing.")

    def handle(self, *args, **opts):
        members = WorkspaceMember.objects.select_related("user", "workspace")
        if opts["workspace"]:
            members = members.filter(workspace__name__icontains=opts["workspace"])

        existing = set(UserPermission.objects.values_list("user_id", "workspace_id"))
        missing = [m for m in members.order_by("workspace__name", "user__email") if (m.user_id, m.workspace_id) not in existing]

        enabled = ", ".join(k for k, v in DEFAULT_NEW_MEMBER_PERMISSIONS.items() if v) or "nothing"
        self.stdout.write(f"{len(missing)} member(s) without a permission row. Default access: {enabled}.")
        for m in missing:
            self.stdout.write(f"  {m.workspace.name:<20} {m.role:<14} {m.user.email}")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN - nothing written."))
            return

        created = 0
        for m in missing:
            _, was_created = UserPermission.objects.get_or_create(
                user=m.user, workspace=m.workspace, defaults=DEFAULT_NEW_MEMBER_PERMISSIONS
            )
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"Created {created} permission row(s)."))
