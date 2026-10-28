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
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from approvals.models import TimeEntryApproval, TimeEntryApprovalItem
from approvals.review import resubmit_entry
from approvals.utils import get_week_bounds
from core.utils.envision_time import envision_local_to_utc
from core.utils.logger import log_activity
from organization_asset.models import AssetUsage, OrganizationAsset
from projects.role_utils import labour_cost, resolve_entry_role, resolve_project_workspace

from .models import TimeEntry
from .rules import ensure_not_future
from .serializers import FieldTicketEntrySerializer, resolve_quantity_used

User = get_user_model()


def _resolve_entry_values(data, existing_entry=None):
    """Validate one payload row and work out everything a TimeEntry needs from it: the target user and
    workspace, the job title and hourly rate (see projects.role_utils), the UTC start/end and the cost.
    Anyone in the project's workspace can be put on a ticket; no project role is needed. When revising, an
    entry that already has a job title/rate keeps it unless the payload names a different job title.
    Raises ValueError on anything that can't be saved."""

    # 1. Resolve target user
    target_user = User.objects.filter(id=data["user"]).first()
    if not target_user:
        raise ValueError(f"User {data['user']} not found.")

    # 2. Workspace — the project's own; the employee just has to be a member of it
    project_id = data["project"]
    try:
        workspace = resolve_project_workspace(target_user, project_id)

        # 3. Job title + hourly rate (see projects.role_utils); a revised entry keeps what it already has
        job_title_id = data.get("job_title") or (existing_entry.job_title_id if existing_entry else None)
        job_title_id, hourly_rate = resolve_entry_role(
            target_user, project_id, workspace, job_title_id, include_deleted_assignments=existing_entry is not None
        )
    except ValidationError as exc:
        detail = exc.detail
        raise ValueError(str(detail[0]) if isinstance(detail, list) else str(detail)) from exc
    if hourly_rate is None and existing_entry is not None:
        hourly_rate = existing_entry.hourly_rate

    # 4. Build UTC-aware datetimes — Field Ticket times from the frontend
    # are always Mountain Time (Envision GEO), regardless of server TIME_ZONE.
    entry_date   = data["date"]
    try:
        ensure_not_future(entry_date)  # today or earlier, never a future date
    except ValidationError as exc:
        raise ValueError(str(exc.detail[0])) from exc
    end_date_val = data.get("end_date") or entry_date

    start_dt = envision_local_to_utc(entry_date, data["start_time"])
    end_dt = envision_local_to_utc(end_date_val, data["end_time"])

    if end_dt <= start_dt:
        raise ValueError("end_time must be after start_time.")

    duration_secs  = (end_dt - start_dt).total_seconds()
    duration_hours = Decimal(duration_secs / 3600).quantize(Decimal("0.01"))

    return {
        "user": target_user,
        "workspace": workspace,
        "job_title_id": job_title_id,
        "hourly_rate": hourly_rate,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "duration_minutes": int(duration_secs // 60),
        "duration_hours": duration_hours,
        "cost": labour_cost(duration_hours, hourly_rate),
    }


def _create_asset_usages(time_entry, assets, duration_hours):
    for asset_item in assets:
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


def sync_approval_total(approval):
    """Recompute an approval's total_hours from its entries that are still active."""
    total_minutes = (
        TimeEntryApprovalItem.objects
        .filter(approval=approval, time_entry__is_deleted=False)
        .aggregate(total=Sum('time_entry__duration'))
        ['total'] or 0
    )
    approval.total_hours = Decimal(total_minutes) / Decimal(60)
    approval.save(update_fields=["total_hours"])


def _approve_entry_in_week(request, time_entry, workspace, user, entry_date):
    """Attach the entry to the user's weekly approval (created if needed) as an approved item."""
    start_week, end_week = get_week_bounds(entry_date)
    approval, _ = TimeEntryApproval.objects.get_or_create(
        workspace=workspace,
        user=user,
        start_date=start_week,
        end_date=end_week,
        defaults={"status": "submitted", "created_by": user},
    )
    TimeEntryApprovalItem.objects.create(
        approval=approval,
        time_entry=time_entry,
        approved=True,
        created_by=request.user,
    )
    return approval


@transaction.atomic
def create_field_ticket_entry(request, data):
    """Validate, create one TimeEntry + its AssetUsages, wire up approval.

    Shared by FieldTicketBulkEntryView and any other endpoint that accepts
    the same per-entry payload shape (e.g. the Envision "field ticket LEM
    from payload" endpoint).
    """
    values = _resolve_entry_values(data)
    entry_date = data["date"]

    # 5. Save TimeEntry
    time_entry = TimeEntry.objects.create(
        user=values["user"],
        workspace=values["workspace"],
        project_id=data["project"],
        job_title_id=values["job_title_id"],
        task_id=data.get("task"),
        description=data.get("description", ""),
        start_time=values["start_dt"],
        end_time=values["end_dt"],
        duration=values["duration_minutes"],
        hourly_rate=values["hourly_rate"],
        cost=values["cost"],
        billable=data.get("billable", False),
        meals=data.get("meals", False),
        hotels=data.get("hotels", False),
        created_by=request.user,
    )

    # 6. Save AssetUsages
    _create_asset_usages(time_entry, data.get("assets", []), values["duration_hours"])

    # 7. Activity log
    log_activity(
        request.user,
        action="CREATE",
        model_name="TimeEntry",
        object_id=time_entry.id,
        request=request,
    )

    # 8. Auto-approval, keeping total_hours on the approval in sync
    approval = _approve_entry_in_week(request, time_entry, values["workspace"], values["user"], entry_date)
    sync_approval_total(approval)

    return time_entry


@transaction.atomic
def update_field_ticket_entry(request, time_entry, data):
    """Apply a payload row to an existing field-ticket TimeEntry (a revision edit).

    Admin override: the entry's lock and its approval do not stop the edit — the approval simply gets its
    total recalculated (and the entry moved to another weekly approval if its employee or week changed).
    Asset usages are replaced by the ones in the payload. Returns the approvals whose totals were touched
    (already synced).
    """
    values = _resolve_entry_values(data, existing_entry=time_entry)
    entry_date = data["date"]

    time_entry.user = values["user"]
    time_entry.workspace = values["workspace"]
    time_entry.project_id = data["project"]
    time_entry.job_title_id = values["job_title_id"]
    time_entry.task_id = data.get("task")
    time_entry.description = data.get("description", "")
    time_entry.start_time = values["start_dt"]
    time_entry.end_time = values["end_dt"]
    time_entry.duration = values["duration_minutes"]
    time_entry.hourly_rate = values["hourly_rate"]
    time_entry.cost = values["cost"]
    time_entry.billable = data.get("billable", False)
    time_entry.meals = data.get("meals", False)
    time_entry.hotels = data.get("hotels", False)
    time_entry.save()

    for usage in list(time_entry.asset_usages.all()):
        usage.delete()
    _create_asset_usages(time_entry, data.get("assets", []), values["duration_hours"])

    log_activity(
        request.user,
        action="UPDATE",
        model_name="TimeEntry",
        object_id=time_entry.id,
        request=request,
    )

    # Re-home the entry if the employee or week no longer matches its approval.
    start_week, end_week = get_week_bounds(entry_date)
    touched = []
    keep = None
    for item in TimeEntryApprovalItem.objects.filter(time_entry=time_entry).select_related("approval"):
        approval = item.approval
        if (
            keep is None
            and approval.user_id == values["user"].id
            and approval.start_date == start_week
            and approval.end_date == end_week
        ):
            keep = item
            if item.status == TimeEntryApprovalItem.STATUS_REJECTED:
                resubmit_entry(time_entry)
            touched.append(approval)
        else:
            item.delete()
            touched.append(approval)
    if keep is None:
        touched.append(_approve_entry_in_week(request, time_entry, values["workspace"], values["user"], entry_date))

    synced = {approval.pk: approval for approval in touched}
    for approval in synced.values():
        if TimeEntryApprovalItem.objects.filter(approval=approval).exists():
            sync_approval_total(approval)
        else:
            approval.delete()
    return list(synced.values())


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
                created = create_field_ticket_entry(request, data)
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
