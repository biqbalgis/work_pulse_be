from rest_framework import viewsets, permissions
from rest_framework.exceptions import ValidationError
from decimal import Decimal

from approvals.models import TimeEntryApprovalItem, TimeEntryApproval
from approvals.utils import get_week_bounds
from .models import TimeEntry
from .serializers import TimeEntrySerializer
from projects.models import ProjectRole
from projects.models import UserProjectRole
from workspaces.models import WorkspaceMember
from core.utils.logger import log_activity


class TimeEntryViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    # -----------------------------------------------------
    # ✔ FILTER BY WORKSPACE FOR SECURITY
    # -----------------------------------------------------
    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return TimeEntry.objects.filter(is_deleted=False)

        workspace_ids = WorkspaceMember.objects.filter(
            user=user
        ).values_list("workspace_id", flat=True)

        return TimeEntry.objects.filter(
            workspace_id__in=workspace_ids,
            is_deleted=False
        )

    # -----------------------------------------------------
    # ✔ OVERRIDE CREATE — CALCULATE RATE, COST, DURATION
    # -----------------------------------------------------

    def perform_create(self, serializer):
        user = self.request.user

        # -------------  EXISTING CODE (untouched) --------------
        wm = WorkspaceMember.objects.filter(user=user).first()
        if not wm:
            raise ValidationError("You are not a member of any workspace.")
        workspace = wm.workspace

        project = serializer.validated_data.get("project")
        job_title = serializer.validated_data.get("job_title")

        upr = UserProjectRole.objects.filter(
            user=user, project=project, job_title=job_title
        ).first()
        if not upr:
            raise ValidationError("You do not have this job title in this project.")
        hourly_rate = upr.hourly_rate

        if hourly_rate is None:
            project_role = ProjectRole.objects.filter(
                project=project, job_title=job_title
            ).first()
            if not project_role:
                raise ValidationError("This job title is not configured for this project.")
            hourly_rate = project_role.hourly_rate

        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        if end_time <= start_time:
            raise ValidationError("end_time must be after start_time.")

        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = Decimal(duration_seconds / 3600).quantize(Decimal("0.01"))
        cost = duration_hours * Decimal(hourly_rate)

        # Save time entry
        time_entry = serializer.save(
            user=user,
            workspace=workspace,
            created_by=user,
            hourly_rate=hourly_rate,
            cost=cost,
            duration=int(duration_seconds // 60),
        )

        # Log activity
        log_activity(
            user,
            action="CREATE",
            model_name="TimeEntry",
            object_id=time_entry.id,
            request=self.request
        )

        # -------------  ✓ ADD THIS BLOCK (ASSET SAVE) --------------
        from organization_asset.models import OrganizationAsset, AssetUsage
        assets_data = serializer.validated_data.get("asset_inputs", [])


        for item in assets_data:
            asset = OrganizationAsset.objects.get(id=item["asset_id"])

            usage = AssetUsage.objects.create(
                time_entry=time_entry,
                asset=asset,
                quantity_used=item.get("quantity_used")  # optional
            )
            usage.cost = usage.calculate_cost(duration_hours)  # cost based on duration or qty
            usage.save()
        # -------------  ✓ END OF ASSET BLOCK --------------

        # -------------  NEW AUTO-APPROVAL CODE --------------
        entry_date = time_entry.start_time.date()
        start_week, end_week = get_week_bounds(entry_date)

        approval, created = TimeEntryApproval.objects.get_or_create(
            workspace=workspace,
            user=user,
            start_date=start_week,
            end_date=end_week,
            defaults={
                "status": "submitted",
                "created_by": user,
            }
        )

        TimeEntryApprovalItem.objects.create(
            approval=approval,
            time_entry=time_entry,
            approved=True,
            created_by=user
        )

        return time_entry

    def perform_update(self, serializer):
        entry = self.get_object()

        # ❌ Prevent editing if approved (locked)
        if entry.is_locked:
            raise ValidationError("This time entry is approved and cannot be edited.")

        # Save basic time entry fields
        updated_entry = serializer.save()

        # ---- Handle asset update ----
        assets_data = self.request.data.get("assets", None)

        if assets_data is not None:
            from organization_asset.models import OrganizationAsset, AssetUsage

            # Delete existing usage to replace with new one
            AssetUsage.objects.filter(time_entry=entry).delete()

            # Recreate asset usage from request
            duration_hours = entry.duration / 60  # convert minutes to hours

            for item in assets_data:
                asset = OrganizationAsset.objects.get(id=item["asset_id"])

                usage = AssetUsage.objects.create(
                    time_entry=entry,
                    asset=asset,
                    quantity_used=item.get("quantity_used")
                )
                usage.cost = usage.calculate_cost(duration_hours)
                usage.save()

        return updated_entry