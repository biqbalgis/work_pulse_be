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

        project = data.get("project", self.instance.project if self.instance else None)
        name = data.get("name", self.instance.name if self.instance else None)
        if project and name:
            qs = OrganizationAsset.objects.filter(
                project=project, name__iexact=name, is_deleted=False,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": "An asset with this name already exists in this project."}
                )
        return data
