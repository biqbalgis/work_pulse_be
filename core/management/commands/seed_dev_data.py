import random
import uuid
from datetime import timedelta, datetime
from django.core.management.base import BaseCommand
from faker import Faker

from users.models import User
from workspaces.models import Workspace, WorkspaceMember
from clients.models import Client
from projects.models import Project, JobTitle, ProjectRole, UserProjectRole
from tasks.models import Task
from time_entries.models import TimeEntry
from organization_asset.models import OrganizationAsset, AssetUsage
from approvals.models import TimeEntryApproval, TimeEntryApprovalItem

fake = Faker()


class Command(BaseCommand):
    help = "Seeds development database with mock data."

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("🚀 Starting DB seed..."))

        self.create_workspaces()
        self.stdout.write(self.style.WARNING("Create Users..."))
        self.create_users()
        self.stdout.write(self.style.WARNING("Create Clients and Projects..."))
        self.create_clients_projects()
        self.stdout.write(self.style.WARNING("Create Tasks..."))
        self.create_tasks_titles()
        self.stdout.write(self.style.WARNING("Create Project Roles..."))
        self.assign_roles_to_projects()
        self.stdout.write(self.style.WARNING("Create Assets..."))
        self.seed_assets()
        self.stdout.write(self.style.WARNING("Create TimeEntry Approvals and Rejections..."))
        self.seed_time_entries_approvals()

        self.stdout.write(self.style.SUCCESS("🎉 DB Seed completed successfully!"))

    # --------------------- CREATE WORKSPACES ---------------------
    def create_workspaces(self):
        self.workspaces = []
        for _ in range(random.randint(3, 5)):
            w = Workspace.objects.create(name=fake.company())
            self.workspaces.append(w)

    # --------------------- CREATE USERS ---------------------
    def create_users(self):
        self.users = []

        # Create a global superuser
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password="admin123",
                first_name="Admin",
                last_name="User",
            )

        ROLES = ["admin", "manager", "user"]

        for _ in range(100):
            emailval = fake.unique.email()
            u = User.objects.create_user(
                username=emailval,
                email=emailval,
                password="password123",
                first_name=fake.first_name(),
                last_name=fake.last_name(),
            )
            # assign user to a random workspace
            ws = random.choice(self.workspaces)
            WorkspaceMember.objects.create(
                user=u,
                workspace=ws,
                role=random.choice(ROLES)
            )
            self.users.append(u)

    # --------------------- CLIENTS + PROJECTS ---------------------
    def create_clients_projects(self):
        self.clients = []
        self.projects = []
        TASKS = ["General", "HR", "Accounting", "IT Support", "GIS", "Surveying", "Engineering", "Admin"]

        for ws in self.workspaces:
            for _ in range(random.randint(8, 12)):  # clients per workspace
                c = Client.objects.create(
                    workspace=ws,
                    name=fake.company()
                )
                self.clients.append(c)

                for _ in range(random.randint(3, 6)):  # projects per client
                    p = Project.objects.create(
                        client=c,
                        workspace=ws,
                        name=fake.bs().title(),
                        billable=random.choice([True, False]),
                        is_active=random.choice([True, True, False]),
                        default_rt_hours = 8,
                        client_hours_rate = 120,
                        ot_multiplier = 2
                    )
                    self.projects.append(p)

                    # create tasks for project
                    for tname in random.sample(TASKS, random.randint(3, 6)):
                        Task.objects.create(project=p, name=tname)

    # --------------------- TASKS + JOB TITLES ---------------------
    def create_tasks_titles(self):
        TITLES = ["CEO", "COO", "CTO", "Developer", "Surveyor", "GIS Analyst", "PM", "Team Lead", "Technician"]

        # JobTitle.name is unique per workspace, not globally — seed a
        # separate set of titles for each workspace rather than one shared
        # global pool.
        self.titles_by_workspace = {}
        for ws in self.workspaces:
            titles = []
            for title in TITLES:
                t, _ = JobTitle.objects.get_or_create(workspace=ws, name=title)
                titles.append(t)
            self.titles_by_workspace[ws.id] = titles

    # --------------------- ASSIGN TITLES + USERS TO PROJECTS ---------------------
    def assign_roles_to_projects(self):
        self.project_roles = []

        for p in self.projects:
            # Pick unique random titles for this project, from its own workspace's pool
            available_titles = random.sample(self.titles_by_workspace[p.workspace_id], random.randint(3, 6))

            for title in available_titles:

                # ---- Create project-level role safely ----
                pr, created = ProjectRole.objects.get_or_create(
                    project=p,
                    job_title=title,
                    defaults={"hourly_rate": random.randint(30, 90)}
                )
                self.project_roles.append(pr)

                # ---- Assign users only from same workspace ----
                eligible_users = [
                    u for u in self.users
                    if WorkspaceMember.objects.filter(user=u, workspace=p.workspace).exists()
                ]

                # If no user in same workspace, skip (rare)
                if not eligible_users:
                    continue

                # pick 1–3 users from this workspace to be assigned this role
                assigned_users = random.sample(eligible_users, random.randint(1, min(3, len(eligible_users))))

                for user in assigned_users:
                    UserProjectRole.objects.get_or_create(
                        user=user,
                        project=p,
                        job_title=title,
                        defaults={"hourly_rate": pr.hourly_rate}
                    )

    # --------------------- ASSETS ---------------------
    def seed_assets(self):
        CHARGE_TYPE = ["hourly", "quantity"]
        for ws in self.workspaces:
            for _ in range(random.randint(3, 6)):
                charge = random.choice(CHARGE_TYPE)
                OrganizationAsset.objects.create(
                    workspace=ws,
                    name=fake.word().title(),
                    charge_type=charge,
                    hourly_rate=random.randint(50, 150) if charge == "hourly" else None,
                    quantity_rate=random.randint(2, 15) if charge == "quantity" else None,
                    total_quantity=random.randint(10, 300)
                )

    # --------------------- TIME ENTRIES + APPROVAL + REJECTIONS ---------------------
    def seed_time_entries_approvals(self):
        for user in self.users:
            ws = WorkspaceMember.objects.filter(user=user).first().workspace
            user_projects = UserProjectRole.objects.filter(user=user)
            if not user_projects:
                continue

            # create 50-120 time entries per user
            for _ in range(random.randint(50, 120)):
                upr = random.choice(user_projects)
                hours = random.randint(1, 8)
                from django.utils import timezone

                start = timezone.make_aware(
                    datetime.now() - timedelta(days=random.randint(1, 60), hours=random.randint(6, 9)))
                end = start + timedelta(hours=hours)

                te = TimeEntry.objects.create(
                    user=user,
                    workspace=ws,
                    project=upr.project,
                    job_title=upr.job_title,
                    start_time=start,
                    end_time=end,
                    duration=hours * 60,
                    hourly_rate=upr.hourly_rate,
                    cost=upr.hourly_rate * hours,
                    billable=random.choice([True, True, False]),
                    meals=random.choice([True, False]),
                    hotels=random.choice([True, False]),
                    description=fake.text(max_nb_chars=random.randint(50, 200)),
                )

                # random asset usage
                assets = OrganizationAsset.objects.filter(workspace=ws)
                for asset in random.sample(list(assets), random.randint(0, 2)):
                    qty = random.randint(1, 10) if asset.charge_type == "quantity" else None
                    AssetUsage.objects.create(
                        time_entry=te,
                        asset=asset,
                        quantity_used=qty,
                        cost=(qty * asset.quantity_rate if qty else hours * asset.hourly_rate)
                    )

                # Auto create approval weekly
                week_start = (start - timedelta(days=start.weekday() + 1)).date()
                week_end = week_start + timedelta(days=6)
                approval, _ = TimeEntryApproval.objects.get_or_create(
                    workspace=ws,
                    user=user,
                    start_date=week_start,
                    end_date=week_end
                )
                approved = random.choice([True, False])
                TimeEntryApprovalItem.objects.create(
                    approval=approval,
                    time_entry=te,
                    approved=approved
                )
                te.is_locked = approved
                te.save()