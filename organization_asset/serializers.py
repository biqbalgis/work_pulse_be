from rest_framework import serializers
from .models import OrganizationAsset


class OrganizationAssetSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    workspace_name = serializers.CharField(source="workspace.name", read_only=True)

    class Meta:
        model = OrganizationAsset
        fields = "__all__"
        read_only_fields = ["id", "workspace", "created_at"]

    def validate(self, data):
        charge_type = data.get("charge_type") or (self.instance.charge_type if self.instance else None)
        if charge_type == "hourly" and not data.get("hourly_rate"):
            raise serializers.ValidationError("Hourly assets require hourly_rate.")
        return data
