from rest_framework import serializers

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
    quantity_used = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)

class TimeEntrySerializer(serializers.ModelSerializer):
    assets = AssetUsageInputSerializer(source="asset_usages", many=True, read_only=True)

    class Meta:
        model = TimeEntry
        fields = [
            "id","user","workspace","project","task","job_title","description",
            "start_time","end_time","duration","hourly_rate","cost","billable",
            "meals","hotels","assets","created_at","created_by"
        ]
        read_only_fields = ["id","user","workspace","duration","hourly_rate","cost",
                            "created_at","created_by","assets"]
