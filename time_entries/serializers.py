from rest_framework import serializers
from django.utils import timezone

from organization_asset.models import AssetUsage
from .models import TimeEntry

class AssetUsageOutputSerializer(serializers.ModelSerializer):
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    charge_type = serializers.CharField(source="asset.charge_type", read_only=True)
    rate = serializers.SerializerMethodField()

    class Meta:
        model = AssetUsage
        fields = ["id", "asset", "asset_name", "charge_type", "quantity_used", "cost", "rate"]

    def get_rate(self, obj):
        if obj.asset.charge_type == "hourly":
            return obj.asset.hourly_rate
        return obj.asset.quantity_rate

class AssetUsageInputSerializer(serializers.Serializer):
    asset_id = serializers.UUIDField()
    # hourly_used and quantity_used both map to the same AssetUsage.quantity_used
    # column — the frontend sends whichever applies to the asset's charge_type.
    # See resolve_quantity_used() below.
    quantity_used = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    hourly_used = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)


def resolve_quantity_used(item):
    """Pick whichever of quantity_used/hourly_used was actually sent — both
    are stored in AssetUsage.quantity_used, whichever is present wins."""
    quantity_used = item.get("quantity_used")
    return quantity_used if quantity_used is not None else item.get("hourly_used")

class TimeEntrySerializer(serializers.ModelSerializer):
    # Output serializer for GET
    assets = AssetUsageOutputSerializer(source="asset_usages", many=True, read_only=True)

    # Input serializer for POST/PATCH — not a model field, handled in perform_create/perform_update
    asset_inputs = AssetUsageInputSerializer(many=True, write_only=True, required=False)

    class Meta:
        model = TimeEntry
        fields = [
            "id","user","workspace","project","task","job_title","description",
            "start_time","end_time","duration","hourly_rate","cost","billable",
            "meals","hotels","assets","asset_inputs","created_at","created_by"
        ]
        read_only_fields = [
            "id","user","workspace","duration","hourly_rate","cost",
            "created_at","created_by","assets"
        ]

    def create(self, validated_data):
        # asset_inputs is not a model field — it's consumed by perform_create
        # after save(). Strip it here so objects.create() doesn't choke on it.
        validated_data.pop("asset_inputs", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("asset_inputs", None)
        return super().update(instance, validated_data)

class BulkTimeEntrySerializer(serializers.Serializer):
    date = serializers.DateField(format="%Y-%m-%d")
    end_date = serializers.DateField(format="%Y-%m-%d", required=False, allow_null=True)
    user = serializers.UUIDField()
    project = serializers.UUIDField()
    job_title = serializers.UUIDField()
    task = serializers.UUIDField(required=False, allow_null=True)
    start_time = serializers.TimeField(format="%H:%M")
    end_time = serializers.TimeField(format="%H:%M")
    description = serializers.CharField(required=False, allow_blank=True)
    billable = serializers.BooleanField(default=False)
    meals = serializers.BooleanField(default=False)
    hotels = serializers.BooleanField(default=False)


class FieldTicketEntrySerializer(serializers.Serializer):
    """One time entry row for the field-ticket bulk endpoint."""
    user        = serializers.UUIDField()
    project     = serializers.UUIDField()
    job_title   = serializers.UUIDField()
    task        = serializers.UUIDField(required=False, allow_null=True)
    date        = serializers.DateField(format="%Y-%m-%d")
    end_date    = serializers.DateField(format="%Y-%m-%d", required=False, allow_null=True)
    start_time  = serializers.TimeField(format="%H:%M")
    end_time    = serializers.TimeField(format="%H:%M")
    description = serializers.CharField(required=False, allow_blank=True)
    billable    = serializers.BooleanField(default=False)
    meals       = serializers.BooleanField(default=False)
    hotels      = serializers.BooleanField(default=False)
    assets      = AssetUsageInputSerializer(many=True, required=False, default=list)


class BulkTimeEntryEditSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    date = serializers.DateField(format="%Y-%m-%d", required=False)
    end_date = serializers.DateField(format="%Y-%m-%d", required=False, allow_null=True)
    user = serializers.CharField(required=False, allow_blank=True)
    user_uuid = serializers.UUIDField(required=False)
    project = serializers.UUIDField(required=False, allow_null=True)
    job_title = serializers.UUIDField(required=False, allow_null=True)
    task = serializers.UUIDField(required=False, allow_null=True)
    start_time = serializers.TimeField(format="%H:%M", required=False)
    end_time = serializers.TimeField(format="%H:%M", required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    billable = serializers.BooleanField(required=False)
    meals = serializers.BooleanField(required=False)
    hotels = serializers.BooleanField(required=False)


class BulkTimeEntryOutputSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    user_uuid = serializers.UUIDField(source="user_id", read_only=True)
    project = serializers.UUIDField(source="project_id", read_only=True, allow_null=True)
    job_title = serializers.UUIDField(source="job_title_id", read_only=True, allow_null=True)
    task = serializers.UUIDField(source="task_id", read_only=True, allow_null=True)
    date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    class Meta:
        model = TimeEntry
        fields = [
            "id",
            "user",
            "user_uuid",
            "project",
            "job_title",
            "task",
            "description",
            "date",
            "end_date",
            "start_time",
            "end_time",
            "billable",
            "meals",
            "hotels",
        ]

    def get_user(self, obj):
        full_name = obj.user.get_full_name().strip()
        return full_name or obj.user.username or obj.user.email or str(obj.user_id)

    def _local_dt(self, dt):
        if dt is None:
            return None
        if timezone.is_aware(dt):
            return timezone.localtime(dt)
        return dt

    def get_date(self, obj):
        dt = self._local_dt(obj.start_time)
        return dt.date().isoformat() if dt else None

    def get_end_date(self, obj):
        dt = self._local_dt(obj.end_time)
        return dt.date().isoformat() if dt else None

    def get_start_time(self, obj):
        dt = self._local_dt(obj.start_time)
        if not dt:
            return None
        return dt.time().replace(second=0, microsecond=0, tzinfo=None).strftime("%H:%M")

    def get_end_time(self, obj):
        dt = self._local_dt(obj.end_time)
        if not dt:
            return None
        return dt.time().replace(second=0, microsecond=0, tzinfo=None).strftime("%H:%M")
