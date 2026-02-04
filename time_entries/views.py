from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from decimal import Decimal
from datetime import datetime, date, timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model


from approvals.models import TimeEntryApprovalItem, TimeEntryApproval
from approvals.utils import get_week_bounds
from .models import TimeEntry
from .serializers import TimeEntrySerializer, BulkTimeEntrySerializer
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
            return TimeEntry.objects.filter(is_deleted=False,user=user)

        workspace_ids = WorkspaceMember.objects.filter(
            user=user
        ).values_list("workspace_id", flat=True)

        return TimeEntry.objects.filter(
            workspace_id__in=workspace_ids,
            is_deleted=False,
            user = user
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


class BulkTimeEntryViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request):
        serializer = BulkTimeEntrySerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        
        created_entries = []
        errors = []
        
        User = get_user_model()
        
        for index, data in enumerate(serializer.validated_data):
            try:
                # 1. Prepare User
                user_id = data['user']
                try:
                    target_user = User.objects.get(id=user_id)
                except User.DoesNotExist:
                    raise ValidationError(f"User {user_id} not found.")

                # 2. Workspace Check
                wm = WorkspaceMember.objects.filter(user=target_user).first()
                if not wm:
                    raise ValidationError(f"User {target_user} is not a member of any workspace.")
                workspace = wm.workspace

                # 3. Project & Role Check
                project_id = data['project']
                job_title_id = data['job_title']
                
                upr = UserProjectRole.objects.filter(
                    user=target_user, project_id=project_id, job_title_id=job_title_id
                ).first()
                
                if upr:
                    hourly_rate = upr.hourly_rate
                else:
                    # Fallback to Project Role
                    project_role = ProjectRole.objects.filter(
                        project_id=project_id, job_title_id=job_title_id
                    ).first()
                    if not project_role:
                        raise ValidationError("This job title is not configured for this project.")
                    hourly_rate = project_role.hourly_rate

                if hourly_rate is None:
                     raise ValidationError("Hourly rate not found.")

                # 4. Dates & Duration
                entry_date = data['date'] # date object
                start_time_obj = data['start_time'] # time object
                end_time_obj = data['end_time'] # time object
                
                # Combine
                start_dt_naive = datetime.combine(entry_date, start_time_obj)
                end_dt_naive = datetime.combine(entry_date, end_time_obj)
                
                # Make aware
                # Assuming simple case: use current timezone or default
                current_tz = timezone.get_current_timezone()
                start_dt = timezone.make_aware(start_dt_naive, current_tz)
                end_dt = timezone.make_aware(end_dt_naive, current_tz)
                
                if end_dt <= start_dt:
                    # Maybe it crosses midnight? For now strict check
                    raise ValidationError("End time must be after start time.")
                
                duration_seconds = (end_dt - start_dt).total_seconds()
                duration_hours = Decimal(duration_seconds / 3600).quantize(Decimal("0.01"))
                cost = duration_hours * Decimal(hourly_rate)

                # 5. Create Time Entry
                time_entry = TimeEntry.objects.create(
                    user=target_user,
                    workspace=workspace,
                    project_id=project_id,
                    job_title_id=job_title_id,
                    task_id=data.get('task'), # handle None
                    description=data.get('description', ''),
                    start_time=start_dt,
                    end_time=end_dt,
                    duration=int(duration_seconds // 60),
                    hourly_rate=hourly_rate,
                    cost=cost,
                    billable=data.get('billable', False),
                    meals=data.get('meals', False),
                    hotels=data.get('hotels', False),
                    created_by=request.user # The caller created it
                )
                
                # 6. Log Activity
                log_activity(
                    request.user,
                    action="CREATE",
                    model_name="TimeEntry",
                    object_id=time_entry.id,
                    request=request
                )

                # 7. Auto Approval
                start_week, end_week = get_week_bounds(entry_date)
                
                approval, _ = TimeEntryApproval.objects.get_or_create(
                    workspace=workspace,
                    user=target_user,
                    start_date=start_week,
                    end_date=end_week,
                    defaults={
                        "status": "submitted",
                        "created_by": target_user # or request.user? Logic in views.py says user
                    }
                )
                
                TimeEntryApprovalItem.objects.create(
                    approval=approval,
                    time_entry=time_entry,
                    approved=True,
                    created_by=request.user
                )

                created_entries.append(time_entry.id)

            except Exception as e:
                errors.append({"index": index, "data": data, "error": str(e)})

        response_status = status.HTTP_201_CREATED
        if errors:
            response_status = status.HTTP_207_MULTI_STATUS if hasattr(status, 'HTTP_207_MULTI_STATUS') else status.HTTP_400_BAD_REQUEST
            # DRF doesn't have 207 by default maybe?
            response_status = 207

        return Response({
            "success_count": len(created_entries),
            "created_ids": created_entries,
            "errors": errors
        }, status=response_status)