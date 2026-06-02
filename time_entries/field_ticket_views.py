"""
Field Ticket Bulk Entry
POST /api/time_entries/field-ticket-entry/

An office user submits time entries on behalf of multiple field users in one
request. Each entry may include one or more assets.
"""

from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from approvals.utils import get_week_bounds
from core.utils.logger import log_activity
from organization_asset.models import AssetUsage, OrganizationAsset
from projects.models import ProjectRole, UserProjectRole
from workspaces.models import WorkspaceMember

from .models import TimeEntry
from .serializers import FieldTicketEntrySerializer

User = get_user_model()


class FieldTicketBulkEntryView(APIView):
    """
    Bulk-create time entries (with optional assets) for multiple field users.

    The authenticated caller must be a workspace member (office user).
    Each item in the array is saved as one TimeEntry for the specified user.

    Request body — array of entry objects:
    [
        {
            "user":        "<uuid>",
            "project":     "<uuid>",
            "job_title":   "<uuid>",
            "task":        "<uuid | null>",          // optional
            "date":        "YYYY-MM-DD",
            "end_date":    "YYYY-MM-DD",             // optional, defaults to date
            "start_time":  "HH:MM",
            "end_time":    "HH:MM",
            "description": "...",                    // optional
            "billable":    false,
            "meals":       false,
            "hotels":      false,
            "assets": [                              // optional
                { "asset_id": "<uuid>", "quantity_used": 2.0 }
            ]
        },
        ...
    ]
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = FieldTicketEntrySerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        created_entries = []
        errors = []

        for index, data in enumerate(serializer.validated_data):
            try:
                created = self._create_single(request, data)
                created_entries.append(str(created.id))
            except Exception as exc:
                errors.append({"index": index, "error": str(exc)})

        resp_status = 201 if not errors else 207
        return Response(
            {
                "success_count": len(created_entries),
                "created_ids":   created_entries,
                "error_count":   len(errors),
                "errors":        errors,
            },
            status=resp_status,
        )

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _create_single(self, request, data):
        """Validate, create one TimeEntry + its AssetUsages, wire up approval."""

        # 1. Resolve target user
        target_user = User.objects.filter(id=data["user"]).first()
        if not target_user:
            raise ValueError(f"User {data['user']} not found.")

        # 2. Workspace
        wm = WorkspaceMember.objects.filter(user=target_user).first()
        if not wm:
            raise ValueError(f"User {target_user} is not a member of any workspace.")
        workspace = wm.workspace

        # 3. Hourly rate — user-project-role first, fallback to project-role
        project_id  = data["project"]
        job_title_id = data["job_title"]

        upr = UserProjectRole.objects.filter(
            user=target_user, project_id=project_id, job_title_id=job_title_id
        ).first()

        if upr and upr.hourly_rate is not None:
            hourly_rate = upr.hourly_rate
        else:
            project_role = ProjectRole.objects.filter(
                project_id=project_id, job_title_id=job_title_id
            ).first()
            if not project_role:
                raise ValueError(
                    f"Job title {job_title_id} is not configured for project {project_id}."
                )
            hourly_rate = project_role.hourly_rate

        if hourly_rate is None:
            raise ValueError("Hourly rate not found for this user/project/job-title combination.")

        # 4. Build timezone-aware datetimes
        entry_date   = data["date"]
        end_date_val = data.get("end_date") or entry_date
        current_tz   = timezone.get_current_timezone()

        start_dt = timezone.make_aware(
            datetime.combine(entry_date, data["start_time"]), current_tz
        )
        end_dt = timezone.make_aware(
            datetime.combine(end_date_val, data["end_time"]), current_tz
        )

        if end_dt <= start_dt:
            raise ValueError("end_time must be after start_time.")

        duration_secs  = (end_dt - start_dt).total_seconds()
        duration_hours = Decimal(duration_secs / 3600).quantize(Decimal("0.01"))
        cost           = duration_hours * Decimal(hourly_rate)

        # 5. Save TimeEntry
        time_entry = TimeEntry.objects.create(
            user=target_user,
            workspace=workspace,
            project_id=project_id,
            job_title_id=job_title_id,
            task_id=data.get("task"),
            description=data.get("description", ""),
            start_time=start_dt,
            end_time=end_dt,
            duration=int(duration_secs // 60),
            hourly_rate=hourly_rate,
            cost=cost,
            billable=data.get("billable", False),
            meals=data.get("meals", False),
            hotels=data.get("hotels", False),
            created_by=request.user,
        )

        # 6. Save AssetUsages
        for asset_item in data.get("assets", []):
            asset = OrganizationAsset.objects.filter(id=asset_item["asset_id"]).first()
            if not asset:
                raise ValueError(f"Asset {asset_item['asset_id']} not found.")

            usage = AssetUsage.objects.create(
                time_entry=time_entry,
                asset=asset,
                quantity_used=asset_item.get("quantity_used"),
            )
            usage.cost = usage.calculate_cost(duration_hours)
            usage.save()

        # 7. Activity log
        log_activity(
            request.user,
            action="CREATE",
            model_name="TimeEntry",
            object_id=time_entry.id,
            request=request,
        )

        # 8. Auto-approval
        start_week, end_week = get_week_bounds(entry_date)
        approval, _ = TimeEntryApproval.objects.get_or_create(
            workspace=workspace,
            user=target_user,
            start_date=start_week,
            end_date=end_week,
            defaults={"status": "submitted", "created_by": target_user},
        )
        TimeEntryApprovalItem.objects.create(
            approval=approval,
            time_entry=time_entry,
            approved=True,
            created_by=request.user,
        )

        return time_entry
