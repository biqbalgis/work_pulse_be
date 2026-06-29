from rest_framework import serializers
from .models import UserPermission


PERMISSION_FIELDS = [
    "dashboard",
    "reports",
    "timesheet",
    "admin_timesheet",
    "field_ticket",
    "time_off",
    "approval",
    "assets",
    "projects",
    "clients",
    "organization",
    "team",
]


class UserPermissionSerializer(serializers.ModelSerializer):
    user_id        = serializers.UUIDField(source="user.id", read_only=True)
    user_full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    user_email     = serializers.EmailField(source="user.email", read_only=True)
    workspace_id   = serializers.UUIDField(source="workspace.id", read_only=True)
    workspace_name = serializers.CharField(source="workspace.name", read_only=True)

    class Meta:
        model  = UserPermission
        fields = [
            "id",
            "user_id", "user_full_name", "user_email",
            "workspace_id", "workspace_name",
            "updated_at",
        ] + PERMISSION_FIELDS
        read_only_fields = [
            "id", "user_id", "user_full_name", "user_email",
            "workspace_id", "workspace_name", "updated_at",
        ]
