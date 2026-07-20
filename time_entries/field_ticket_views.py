"""
Field Ticket Bulk Entry
POST /api/time_entries/field-ticket-entry/

An office user submits time entries on behalf of multiple field users in one
request. Each entry may include one or more assets.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from approvals.utils import get_week_bounds
from core.utils.envision_time import envision_local_to_utc
from core.utils.logger import log_activity
from organization_asset.models import AssetUsage, OrganizationAsset
from projects.models import ProjectRole, UserProjectRole
from workspaces.models import WorkspaceMember

from .models import TimeEntry
from .serializers import FieldTicketEntrySerializer, resolve_quantity_used

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

    @transaction.atomic
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

        # 4. Build UTC-aware datetimes — Field Ticket times from the frontend
        # are always Mountain Time (Envision GEO), regardless of server TIME_ZONE.
        entry_date   = data["date"]
        end_date_val = data.get("end_date") or entry_date

        start_dt = envision_local_to_utc(entry_date, data["start_time"])
        end_dt = envision_local_to_utc(end_date_val, data["end_time"])

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
                quantity_used=resolve_quantity_used(asset_item),
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

        # Keep total_hours on the approval in sync
        total_minutes = (
            TimeEntryApprovalItem.objects
            .filter(approval=approval, time_entry__is_deleted=False)
            .aggregate(total=Sum('time_entry__duration'))
            ['total'] or 0
        )
        approval.total_hours = Decimal(total_minutes) / Decimal(60)
        approval.save(update_fields=["total_hours"])

        return time_entry


class FixMissingApprovalsView(APIView):
    """
    POST /api/time_entries/fix-missing-approvals/

    Admin / superuser only.
    Backfills TimeEntryApproval + TimeEntryApprovalItem for every TimeEntry
    that has no approval record.

    Optional body params:
        user_id  (uuid)  — limit fix to one user
        dry_run  (bool)  — if true, returns what WOULD be fixed without saving
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Restrict to superuser or workspace admin
        from workspaces.models import WorkspaceMember
        is_admin = (
            request.user.is_superuser or
            WorkspaceMember.objects.filter(user=request.user, role__in=["admin", "manager"]).exists()
        )
        if not is_admin:
            return Response({"error": "Only admins can run this fix."}, status=403)

        dry_run    = request.data.get("dry_run", False)
        user_id    = request.data.get("user_id")

        orphaned_qs = (
            TimeEntry.objects
            .filter(is_deleted=False)
            .exclude(
                id__in=TimeEntryApprovalItem.objects.values_list("time_entry_id", flat=True)
            )
            .select_related("user", "workspace")
            .order_by("user_id", "start_time")
        )

        if user_id:
            orphaned_qs = orphaned_qs.filter(user_id=user_id)

        fixed   = []
        skipped = []

        for entry in orphaned_qs:
            user      = entry.user
            workspace = entry.workspace

            if not workspace:
                wm = WorkspaceMember.objects.filter(user=user).first()
                if not wm:
                    skipped.append({"entry_id": str(entry.id), "reason": "No workspace membership."})
                    continue
                workspace = wm.workspace

            entry_date           = entry.start_time.date()
            start_week, end_week = get_week_bounds(entry_date)

            record = {
                "entry_id":   str(entry.id),
                "user":       user.get_full_name(),
                "date":       str(entry_date),
                "week_start": str(start_week),
                "week_end":   str(end_week),
            }

            if dry_run:
                fixed.append(record)
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
                        created_by=request.user,
                    )
                    total_minutes = (
                        TimeEntryApprovalItem.objects
                        .filter(approval=approval, time_entry__is_deleted=False)
                        .aggregate(total=Sum("time_entry__duration"))["total"] or 0
                    )
                    approval.total_hours = Decimal(total_minutes) / Decimal(60)
                    approval.save(update_fields=["total_hours"])

                record["approval_id"]      = str(approval.id)
                record["approval_created"] = created
                fixed.append(record)

            except Exception as exc:
                skipped.append({"entry_id": str(entry.id), "reason": str(exc)})

        return Response({
            "dry_run":       dry_run,
            "fixed_count":   len(fixed),
            "skipped_count": len(skipped),
            "fixed":         fixed,
            "skipped":       skipped,
        })
