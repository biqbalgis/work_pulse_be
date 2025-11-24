from rest_framework import serializers
from .models import OrganizationAsset

class OrganizationAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationAsset
        fields = "__all__"
        read_only_fields = ["id", "workspace", "created_at"]

    def validate(self, data):
        charge_type = data.get("charge_type") or self.instance.charge_type

        # Validate hourly asset must have hourly_rate
        if charge_type == "hourly" and not data.get("hourly_rate"):
            raise serializers.ValidationError("Hourly assets require hourly_rate.")

        # Validate quantity asset must have quantity_rate
        if charge_type == "quantity" and not data.get("quantity_rate"):
            raise serializers.ValidationError("Quantity assets require quantity_rate.")

        return data
