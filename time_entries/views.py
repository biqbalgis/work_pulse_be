from rest_framework import viewsets, permissions
from rest_framework.exceptions import ValidationError
from decimal import Decimal
from .models import TimeEntry
from .serializers import TimeEntrySerializer
from projects.models import ProjectRole
from projects.models import UserProjectRole
from workspaces.models import WorkspaceMember
from core.utils.logger import log_activity


class TimeEntryViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

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

        # user's workspace
        wm = WorkspaceMember.objects.filter(user=user).first()
        if not wm:
            raise ValidationError("You are not a member of any workspace.")

        workspace = wm.workspace

        project = serializer.validated_data.get("project")
        job_title = serializer.validated_data.get("job_title")

        # -----------------------------------------------------
        # 1. Validate: user has the job title in this project
        # -----------------------------------------------------
        upr = UserProjectRole.objects.filter(
            user=user, project=project, job_title=job_title
        ).first()

        if not upr:
            raise ValidationError("You do not have this job title in this project.")

        # -----------------------------------------------------
        # 2. Determine hourly_rate
        # -----------------------------------------------------
        hourly_rate = upr.hourly_rate

        if hourly_rate is None:
            project_role = ProjectRole.objects.filter(
                project=project, job_title=job_title
            ).first()

            if not project_role:
                raise ValidationError("This job title is not configured for this project.")

            hourly_rate = project_role.hourly_rate

        # -----------------------------------------------------
        # 3. Calculate duration and cost
        # -----------------------------------------------------
        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        if end_time <= start_time:
            raise ValidationError("end_time must be after start_time.")

        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = Decimal(duration_seconds / 3600).quantize(Decimal("0.01"))

        cost = duration_hours * Decimal(hourly_rate)

        # -----------------------------------------------------
        # 4. Save
        # -----------------------------------------------------
        time_entry = serializer.save(
            user=user,
            workspace=workspace,
            created_by=user,
            hourly_rate=hourly_rate,
            cost=cost,
            duration=int(duration_seconds // 60),  # store minutes
        )

        # -----------------------------------------------------
        # 5. Log Activity
        # -----------------------------------------------------
        log_activity(
            user,
            action="CREATE",
            model_name="TimeEntry",
            object_id=time_entry.id,
            request=self.request
        )
