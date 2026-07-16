from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from django.db.models import Sum
from collections import defaultdict
from decimal import Decimal
from datetime import datetime, date, timedelta
from uuid import UUID
from django.utils import timezone
from django.contrib.auth import get_user_model


from approvals.models import TimeEntryApprovalItem, TimeEntryApproval
from approvals.utils import get_week_bounds
from reports.models import LEMReport
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
            queryset = TimeEntry.objects.filter(is_deleted=False, user=user)
        else:
            workspace_ids = WorkspaceMember.objects.filter(
                user=user
            ).values_list("workspace_id", flat=True)

            queryset = TimeEntry.objects.filter(
                workspace_id__in=workspace_ids,
                is_deleted=False,
                user=user
            )

        return self._filter_by_date_range(queryset)

    def _filter_by_date_range(self, queryset):
        """Apply ?start_date=&end_date= as an inclusive range on start_time's date.
        Accepts plain YYYY-MM-DD or a full ISO datetime (any time component is ignored).
        """
        start_date_param = self.request.query_params.get("start_date")
        end_date_param = self.request.query_params.get("end_date")

        if not start_date_param and not end_date_param:
            return queryset

        if not start_date_param:
            start_date_param = end_date_param
        if not end_date_param:
            end_date_param = start_date_param

        try:
            start_date = datetime.strptime(str(start_date_param).split("T")[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(str(end_date_param).split("T")[0], "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError("start_date/end_date must be in YYYY-MM-DD format.")

        return queryset.filter(start_time__date__range=(start_date, end_date))

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
        old_start_time = entry.start_time

        # ❌ Prevent editing if approved (locked)
        if entry.is_locked:
            raise ValidationError("This time entry is approved and cannot be edited.")

        validated_data = serializer.validated_data
        project = validated_data.get("project", entry.project)
        job_title = validated_data.get("job_title", entry.job_title)
        start_time = validated_data.get("start_time", entry.start_time)
        end_time = validated_data.get("end_time", entry.end_time)

        if end_time and end_time <= start_time:
            raise ValidationError("end_time must be after start_time.")

        hourly_rate = entry.hourly_rate
        cost = entry.cost
        duration_minutes = entry.duration
        duration_hours_decimal = Decimal(duration_minutes) / Decimal("60")

        if end_time:
            duration_seconds = (end_time - start_time).total_seconds()
            duration_minutes = int(duration_seconds // 60)
            duration_hours_decimal = Decimal(duration_seconds / 3600).quantize(Decimal("0.01"))

        if project and job_title and end_time:
            upr = UserProjectRole.objects.filter(
                user=entry.user, project=project, job_title=job_title
            ).first()
            if upr and upr.hourly_rate is not None:
                hourly_rate = upr.hourly_rate
            else:
                project_role = ProjectRole.objects.filter(
                    project=project, job_title=job_title
                ).first()
                if not project_role:
                    raise ValidationError("This job title is not configured for this project.")
                hourly_rate = project_role.hourly_rate

            if hourly_rate is None:
                raise ValidationError("Hourly rate not found.")

            cost = duration_hours_decimal * Decimal(hourly_rate)

        # Save editable fields first, then force derived fields onto the model.
        updated_entry = serializer.save()
        updated_entry.duration = duration_minutes
        updated_entry.hourly_rate = hourly_rate
        updated_entry.cost = cost
        updated_entry.save(update_fields=["duration", "hourly_rate", "cost"])

        # ---- Handle asset update ----
        assets_data = validated_data.get("asset_inputs", None)

        if assets_data is not None:
            from organization_asset.models import OrganizationAsset, AssetUsage

            # Delete existing usage to replace with new one
            AssetUsage.objects.filter(time_entry=entry).delete()

            for item in assets_data:
                asset = OrganizationAsset.objects.get(id=item["asset_id"])

                usage = AssetUsage.objects.create(
                    time_entry=entry,
                    asset=asset,
                    quantity_used=item.get("quantity_used")
                )
                usage.cost = usage.calculate_cost(duration_hours_decimal)
                usage.save()

        old_week = get_week_bounds(old_start_time.date())
        new_week = get_week_bounds(updated_entry.start_time.date())

        if old_week != new_week:
            old_approval = (
                TimeEntryApproval.objects.filter(
                    user=entry.user,
                    workspace=entry.workspace,
                    start_date=old_week[0],
                    end_date=old_week[1],
                ).first()
            )
            if old_approval:
                TimeEntryApprovalItem.objects.filter(
                    approval=old_approval,
                    time_entry=updated_entry,
                ).delete()
                self._sync_approval_after_delete(old_approval)

            new_approval, _ = TimeEntryApproval.objects.get_or_create(
                workspace=entry.workspace,
                user=entry.user,
                start_date=new_week[0],
                end_date=new_week[1],
                defaults={
                    "status": "submitted",
                    "created_by": entry.user,
                }
            )
            TimeEntryApprovalItem.objects.get_or_create(
                approval=new_approval,
                time_entry=updated_entry,
                defaults={
                    "approved": True,
                    "created_by": entry.user,
                }
            )
            self._sync_approval_after_delete(new_approval)
        else:
            approval_ids = TimeEntryApprovalItem.objects.filter(time_entry=updated_entry).values_list("approval_id", flat=True)
            for approval in TimeEntryApproval.objects.filter(id__in=approval_ids):
                self._sync_approval_after_delete(approval)

        return updated_entry

    def _sync_approval_after_delete(self, approval):
        active_items = TimeEntryApprovalItem.objects.filter(approval=approval)

        if not active_items.exists():
            approval.delete()
            return

        total_minutes = (
            active_items.filter(time_entry__is_deleted=False)
            .aggregate(total_minutes=Sum("time_entry__duration"))
            .get("total_minutes")
            or 0
        )
        approval.total_hours = Decimal(total_minutes) / Decimal("60")
        approval.save(update_fields=["total_hours"])

    def _entry_dates(self, instance):
        start_date = timezone.localtime(instance.start_time).date() if timezone.is_aware(instance.start_time) else instance.start_time.date()
        end_dt = instance.end_time or instance.start_time
        end_date = timezone.localtime(end_dt).date() if timezone.is_aware(end_dt) else end_dt.date()

        current_date = start_date
        dates = set()
        while current_date <= end_date:
            dates.add(current_date.isoformat())
            current_date += timedelta(days=1)
        return dates

    def _normalize_text(self, value):
        return " ".join(str(value or "").strip().lower().split())

    def _lem_contains_entry(self, report_data, entry_dates, employee_name, job_title):
        if not isinstance(report_data, dict):
            return False

        report_date = report_data.get("date")
        if report_date in entry_dates:
            for row in report_data.get("rows", []):
                row_name = self._normalize_text(row.get("name") or row.get("employee_name"))
                row_role = self._normalize_text(row.get("role") or row.get("job_title"))
                if row_name == employee_name and (not row_role or row_role == job_title):
                    return True

        from_date = report_data.get("from_date")
        to_date = report_data.get("to_date")
        if from_date and to_date:
            for report_day in report_data.get("daily_reports", []):
                if report_day.get("date") not in entry_dates:
                    continue
                for employee in report_day.get("employees", []):
                    row_name = self._normalize_text(employee.get("name"))
                    if row_name == employee_name:
                        return True

        nested_report_data = report_data.get("report_data")
        if isinstance(nested_report_data, dict):
            for report_day_key, report_day in nested_report_data.items():
                if report_day_key not in entry_dates:
                    continue
                for row in report_day.get("time_entries", []):
                    row_name = self._normalize_text(row.get("name"))
                    row_role = self._normalize_text(row.get("role"))
                    if row_name == employee_name and (not row_role or row_role == job_title):
                        return True

        return False

    def perform_destroy(self, instance):
        employee_full_name = instance.user.get_full_name().strip() or f"{instance.user.first_name} {instance.user.last_name}".strip() or getattr(instance.user, "username", "")
        employee_name = self._normalize_text(employee_full_name)
        job_title = self._normalize_text(instance.job_title.name if instance.job_title else "")
        entry_dates = self._entry_dates(instance)

        lem_reports = LEMReport.objects.filter(project=instance.project)
        if any(self._lem_contains_entry(lem_report.report_data, entry_dates, employee_name, job_title) for lem_report in lem_reports):
            raise ValidationError("This time entry is already part of a LEM and cannot be deleted.")

        approvals = list(
            TimeEntryApproval.objects.filter(items__time_entry=instance).distinct()
        )

        for asset_usage in instance.asset_usages.all():
            asset_usage.delete()

        for approval_item in TimeEntryApprovalItem.objects.filter(time_entry=instance):
            approval_item.delete()

        instance.delete()

        for approval in approvals:
            self._sync_approval_after_delete(approval)

        log_activity(
            self.request.user,
            action="DELETE",
            model_name="TimeEntry",
            object_id=instance.id,
            request=self.request
        )


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

    def _as_uuid_string(self, value):
        if value in (None, ""):
            return None
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return None

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

        queryset = TimeEntry.objects.select_related("user").filter(
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

                submitted_user_id = self._as_uuid_string(data.get("user_uuid")) or self._as_uuid_string(data.get("user"))
                if submitted_user_id and submitted_user_id != str(entry.user_id):
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


class WeeklyHoursSummaryView(APIView):
    """
    POST /api/time_entries/weekly-hours-summary/

    Body params:
        start_date  (YYYY-MM-DD)  — first day of the week  [required]
        end_date    (YYYY-MM-DD)  — last day of the week   [required]
        project_id  (uuid)        — filter to one project  [optional]

    Returns day-by-day hours per project, a weekly total per project,
    and a grand total across all projects.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data

        start_str = data.get("start_date")
        end_str   = data.get("end_date")

        if not start_str or not end_str:
            return Response(
                {"error": "start_date and end_date are required (YYYY-MM-DD)"},
                status=400,
            )

        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        if end_date < start_date:
            return Response({"error": "end_date must be >= start_date"}, status=400)

        # Build ordered list of dates in the requested range
        week_days = []
        cursor = start_date
        while cursor <= end_date:
            week_days.append(cursor)
            cursor += timedelta(days=1)

        # Base queryset — scoped to the authenticated user
        qs = (
            TimeEntry.objects
            .filter(
                user=request.user,
                is_deleted=False,
                start_time__date__gte=start_date,
                start_time__date__lte=end_date,
            )
            .select_related("project")
        )

        # Optional single-project filter
        project_id = data.get("project_id")
        if project_id:
            qs = qs.filter(project_id=project_id)

        # Aggregate: total duration (minutes) per project per day
        rows = (
            qs
            .values("project_id", "project__name", "start_time__date")
            .annotate(total_minutes=Sum("duration"))
            .order_by("project__name", "start_time__date")
        )

        # Shape into {project_id: {day: hours, ...}}
        day_strings  = [d.isoformat() for d in week_days]
        project_meta = {}   # project_id → name
        project_days = defaultdict(lambda: defaultdict(float))

        for row in rows:
            pid   = str(row["project_id"]) if row["project_id"] else "__no_project__"
            pname = row["project__name"]   or "No Project"
            day   = row["start_time__date"].isoformat()
            hrs   = round((row["total_minutes"] or 0) / 60, 2)

            project_meta[pid] = pname
            project_days[pid][day] += hrs

        # Build response projects list
        projects_out = []
        grand_total  = 0.0

        for pid, pname in project_meta.items():
            daily = {d: round(project_days[pid].get(d, 0.0), 2) for d in day_strings}
            weekly_total = round(sum(daily.values()), 2)
            grand_total += weekly_total

            projects_out.append({
                "project_id":    pid,
                "project_name":  pname,
                "daily_hours":   daily,
                "weekly_total":  weekly_total,
            })

        # Sort alphabetically by project name
        projects_out.sort(key=lambda x: x["project_name"])

        return Response({
            "week":        day_strings,
            "projects":    projects_out,
            "grand_total": round(grand_total, 2),
        })
