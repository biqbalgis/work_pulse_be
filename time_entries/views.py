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
from .serializers import (
    TimeEntrySerializer,
    BulkTimeEntrySerializer,
    BulkTimeEntryEditSerializer,
    BulkTimeEntryOutputSerializer,
)
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
                end_date_val = data.get('end_date') or entry_date
                start_time_obj = data['start_time'] # time object
                end_time_obj = data['end_time'] # time object
                
                # Combine
                start_dt_naive = datetime.combine(entry_date, start_time_obj)
                end_dt_naive = datetime.combine(end_date_val, end_time_obj)
                
                # Make aware
                # Assuming simple case: use current timezone or default
                current_tz = timezone.get_current_timezone()
                start_dt = timezone.make_aware(start_dt_naive, current_tz)
                end_dt = timezone.make_aware(end_dt_naive, current_tz)
                
                if end_dt <= start_dt:
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


class BulkTimeEntryEditViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser:
            return TimeEntry.objects.filter(is_deleted=False)

        memberships = WorkspaceMember.objects.filter(
            user=user,
            is_active=True,
            is_deleted=False,
        )
        workspace_ids = memberships.values_list("workspace_id", flat=True)
        elevated_roles = {"admin", "manager", "field_manager"}
        has_workspace_wide_access = memberships.filter(role__in=elevated_roles).exists()

        queryset = TimeEntry.objects.filter(
            workspace_id__in=workspace_ids,
            is_deleted=False,
        )

        if has_workspace_wide_access:
            return queryset

        return queryset.filter(user=user)

    def _filter_queryset(self, queryset, params):
        def _get(key):
            if hasattr(params, "get"):
                return params.get(key)
            return None

        ids_param = _get("ids")
        date_param = _get("date")
        project_id_param = _get("projects_id") or _get("project_id")
        start_date_param = _get("start_date")
        end_date_param = _get("end_date")

        if ids_param:
            if isinstance(ids_param, (list, tuple)):
                ids = [str(item) for item in ids_param if item]
            else:
                ids = [item.strip() for item in str(ids_param).split(",") if item.strip()]
            queryset = queryset.filter(id__in=ids)

        if date_param:
            if isinstance(date_param, date):
                target_date = date_param
            else:
                try:
                    target_date = datetime.strptime(str(date_param), "%Y-%m-%d").date()
                except ValueError:
                    raise ValidationError("date must be in YYYY-MM-DD format.")
            queryset = queryset.filter(start_time__date=target_date)
        elif start_date_param or end_date_param:
            if not start_date_param:
                start_date_param = end_date_param
            if not end_date_param:
                end_date_param = start_date_param
            try:
                start_date = datetime.strptime(str(start_date_param), "%Y-%m-%d").date()
                end_date = datetime.strptime(str(end_date_param), "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError("start_date/end_date must be in YYYY-MM-DD format.")
            queryset = queryset.filter(start_time__date__range=(start_date, end_date))

        if project_id_param:
            queryset = queryset.filter(project_id=project_id_param)

        return queryset

    def list(self, request):
        queryset = self._filter_queryset(self.get_queryset(), request.query_params)
        serializer = BulkTimeEntryOutputSerializer(queryset, many=True)
        return Response(serializer.data)

    def _local_dt(self, dt):
        if dt is None:
            return None
        if timezone.is_aware(dt):
            return timezone.localtime(dt)
        return dt

    def _local_time(self, dt):
        local_dt = self._local_dt(dt)
        if not local_dt:
            return None
        return local_dt.time().replace(second=0, microsecond=0, tzinfo=None)

    def create(self, request):
        if isinstance(request.data, dict):
            queryset = self._filter_queryset(self.get_queryset(), request.data)
            serializer = BulkTimeEntryOutputSerializer(queryset, many=True)
            return Response(serializer.data)

        serializer = BulkTimeEntryEditSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        updated_entries = []
        errors = []

        for index, data in enumerate(serializer.validated_data):
            try:
                entry_id = data["id"]
                entry = self.get_queryset().filter(id=entry_id).first()
                if not entry:
                    raise ValidationError(f"Time entry {entry_id} not found.")

                if entry.is_locked:
                    raise ValidationError("This time entry is approved and cannot be edited.")

                local_start_dt = self._local_dt(entry.start_time)
                local_end_dt = self._local_dt(entry.end_time) if entry.end_time else None

                entry_date = data.get("date") or local_start_dt.date()
                start_time_val = data.get("start_time") or self._local_time(entry.start_time)
                if start_time_val is None:
                    raise ValidationError("start_time is required.")

                if "end_date" in data:
                    end_date_val = data.get("end_date") or entry_date
                else:
                    end_date_val = local_end_dt.date() if local_end_dt else entry_date

                end_time_val = data.get("end_time") or self._local_time(entry.end_time)
                if end_time_val is None:
                    raise ValidationError("end_time is required.")

                start_dt_naive = datetime.combine(entry_date, start_time_val)
                end_dt_naive = datetime.combine(end_date_val, end_time_val)

                current_tz = timezone.get_current_timezone()
                start_dt = timezone.make_aware(start_dt_naive, current_tz)
                end_dt = timezone.make_aware(end_dt_naive, current_tz)

                if end_dt <= start_dt:
                    raise ValidationError("End time must be after start time.")

                if "user" in data and str(data.get("user")) != str(entry.user_id):
                    raise ValidationError("user does not match the time entry owner.")

                if "project" in data:
                    project_id = data.get("project")
                else:
                    project_id = entry.project_id

                if "job_title" in data:
                    job_title_id = data.get("job_title")
                else:
                    job_title_id = entry.job_title_id

                if not project_id or not job_title_id:
                    raise ValidationError("project and job_title are required.")

                upr = UserProjectRole.objects.filter(
                    user=entry.user, project_id=project_id, job_title_id=job_title_id
                ).first()

                if upr:
                    hourly_rate = upr.hourly_rate
                else:
                    project_role = ProjectRole.objects.filter(
                        project_id=project_id, job_title_id=job_title_id
                    ).first()
                    if not project_role:
                        raise ValidationError("This job title is not configured for this project.")
                    hourly_rate = project_role.hourly_rate

                if hourly_rate is None:
                    raise ValidationError("Hourly rate not found.")

                duration_seconds = (end_dt - start_dt).total_seconds()
                duration_hours = Decimal(duration_seconds / 3600).quantize(Decimal("0.01"))
                cost = duration_hours * Decimal(hourly_rate)

                entry.project_id = project_id
                entry.job_title_id = job_title_id
                if "task" in data:
                    entry.task_id = data.get("task")
                if "description" in data:
                    entry.description = data.get("description", "")
                if "billable" in data:
                    entry.billable = data.get("billable")
                if "meals" in data:
                    entry.meals = data.get("meals")
                if "hotels" in data:
                    entry.hotels = data.get("hotels")

                entry.start_time = start_dt
                entry.end_time = end_dt
                entry.duration = int(duration_seconds // 60)
                entry.hourly_rate = hourly_rate
                entry.cost = cost
                entry.save()

                log_activity(
                    request.user,
                    action="UPDATE",
                    model_name="TimeEntry",
                    object_id=entry.id,
                    request=request
                )

                updated_entries.append(entry.id)

            except Exception as e:
                errors.append({"index": index, "id": str(data.get("id")), "error": str(e)})

        response_status = status.HTTP_200_OK
        if errors:
            response_status = 207

        return Response({
            "success_count": len(updated_entries),
            "updated_ids": updated_entries,
            "errors": errors
        }, status=response_status)
