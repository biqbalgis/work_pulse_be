from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from core.utils.logger import log_activity
from projects.models import UserProjectRole, ProjectRole
from decimal import Decimal
from time_entries.serializers import TimeEntrySerializer


class TimeEntryViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntrySerializer
    permission_classes = [...]

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data.get("project")
        job_title = serializer.validated_data.get("job_title")

        # Validate: user must have this job title in this project
        upr = UserProjectRole.objects.filter(
            user=user, project=project, job_title=job_title
        ).first()

        if not upr:
            raise ValidationError("You do not have this job title in this project.")

        # Priority: User-specific hourly rate OR Project default rate
        hourly_rate = upr.hourly_rate
        if hourly_rate is None:
            pr = ProjectRole.objects.filter(
                project=project, job_title=job_title
            ).first()
            if not pr:
                raise ValidationError("This job title is not configured for this project.")
            hourly_rate = pr.hourly_rate

        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        # Auto duration in hours (rounded)
        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = Decimal(duration_seconds / 3600).quantize(Decimal('0.01'))

        cost = duration_hours * hourly_rate

        time_entry = serializer.save(
            user=user,
            hourly_rate=hourly_rate,
            cost=cost,
            duration=int(duration_seconds // 60)  # minutes
        )

        log_activity(user, "CREATE", "TimeEntry", time_entry.id, request=self.request)
