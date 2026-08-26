"""
Tests for Envision GEO LEM Report API
Run: python manage.py test reports.tests_envision
"""

import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from clients.models import Client
from organization_asset.models import AssetUsage, OrganizationAsset
from projects.models import JobTitle, Project
from time_entries.models import TimeEntry
from users.models import User
from workspaces.models import Workspace, WorkspaceMember


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


class EnvisionLEMReportTest(TestCase):

    def setUp(self):
        self.client_api = APIClient()

        # ── Workspace (with address and pm_info stored in DB) ─────────────────
        self.workspace = Workspace.objects.create(
            name="Envision GEO",
            address="1201 5 St SW, Suite 203, Calgary AB T2R 2Y6",
            pm_info={
                "pm_name": "Tyson Bancroft",
                "contact": "accounting@envisiongeo.ca",
                "phone":   "403-902-1221",
            },
        )

        # ── User (admin) ──────────────────────────────────────────────────────
        self.user = User.objects.create_user(
            username="tyson",
            email="tyson@envisiongeo.ca",
            password="testpass123",
            first_name="Tyson",
            last_name="Bancroft",
        )
        WorkspaceMember.objects.create(
            workspace=self.workspace,
            user=self.user,
            role="admin",
        )

        token = get_tokens_for_user(self.user)
        self.client_api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # ── Second team member ────────────────────────────────────────────────
        self.user2 = User.objects.create_user(
            username="sarah",
            email="sarah@envisiongeo.ca",
            password="testpass123",
            first_name="Sarah",
            last_name="Lee",
        )
        WorkspaceMember.objects.create(
            workspace=self.workspace,
            user=self.user2,
            role="user",
        )

        # ── Client ────────────────────────────────────────────────────────────
        self.client_obj = Client.objects.create(
            workspace=self.workspace,
            name="Kiewit Construction",
        )

        # ── Project (with job_code stored in DB) ─────────────────────────────
        self.project = Project.objects.create(
            workspace=self.workspace,
            client=self.client_obj,
            name="Highway 2 Overpass",
            job_code="260142",
            is_active=True,
        )

        # ── Job Titles ────────────────────────────────────────────────────────
        self.jt_pc = JobTitle.objects.create(name="PC")
        self.jt_fs = JobTitle.objects.create(name="FS")

        # ── Report date ───────────────────────────────────────────────────────
        self.report_date = "2026-06-01"
        start = datetime(2026, 6, 1, 8, 0, tzinfo=dt_timezone.utc)
        end   = datetime(2026, 6, 1, 16, 0, tzinfo=dt_timezone.utc)

        # ── Time entries ──────────────────────────────────────────────────────
        self.entry1 = TimeEntry.objects.create(
            user=self.user,
            workspace=self.workspace,
            project=self.project,
            job_title=self.jt_pc,
            start_time=start,
            end_time=end,
            duration=480,          # 8 hours in minutes
            hourly_rate=Decimal("75.00"),
            cost=Decimal("600.00"),
            meals=True,
            hotels=False,
            description="Set up base station; performed bridge deck layout.",
        )
        self.entry2 = TimeEntry.objects.create(
            user=self.user2,
            workspace=self.workspace,
            project=self.project,
            job_title=self.jt_fs,
            start_time=start,
            end_time=end,
            duration=480,
            hourly_rate=Decimal("65.00"),
            cost=Decimal("520.00"),
            meals=False,
            hotels=False,
            description="UAV mapping and field survey.",
        )

        # ── Assets ────────────────────────────────────────────────────────────
        self.asset_opfc = OrganizationAsset.objects.create(
            workspace=self.workspace,
            name="OPFC",
            charge_type="hourly",
            hourly_rate=Decimal("150.00"),
        )
        self.asset_uav = OrganizationAsset.objects.create(
            workspace=self.workspace,
            name="UAV",
            charge_type="quantity",
            quantity_rate=Decimal("250.00"),
        )

        AssetUsage.objects.create(
            time_entry=self.entry1,
            asset=self.asset_opfc,
            cost=Decimal("1200.00"),
        )
        AssetUsage.objects.create(
            time_entry=self.entry2,
            asset=self.asset_uav,
            quantity_used=Decimal("1"),
            cost=Decimal("250.00"),
        )

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_lem_returns_pdf(self):
        """Should return a PDF file response."""
        response = self.client_api.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {
                "project_id": str(self.project.id),
                "date": self.report_date,
                "client_rep": "John Doe",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("Envision_LEM_LEM-FT-", response["Content-Disposition"])

    def test_lem_number_is_sequential(self):
        """Each call should produce the next LEM number."""
        payload = {"project_id": str(self.project.id), "date": self.report_date}
        r1 = self.client_api.post("/api/reports/envision/fieldTicket_Lem/", payload, format="json")
        r2 = self.client_api.post("/api/reports/envision/fieldTicket_Lem/", payload, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        # LEM-001 then LEM-002
        self.assertIn("LEM-FT-001", r1["Content-Disposition"])
        self.assertIn("LEM-FT-002", r2["Content-Disposition"])

    def test_missing_project_id_returns_400(self):
        response = self.client_api.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {"date": self.report_date},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_date_returns_400(self):
        response = self.client_api.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {"project_id": str(self.project.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_project_id_returns_404(self):
        response = self.client_api.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {"project_id": str(uuid.uuid4()), "date": self.report_date},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_returns_401(self):
        unauth = APIClient()
        response = unauth.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {"project_id": str(self.project.id), "date": self.report_date},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_signed_pdf(self):
        """sign=True should include sign_name in disposition without error."""
        response = self.client_api.post(
            "/api/reports/envision/fieldTicket_Lem/",
            {
                "project_id": str(self.project.id),
                "date": self.report_date,
                "sign": True,
                "sign_name": "Tyson Bancroft",
                "sign_date": "2026-06-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
