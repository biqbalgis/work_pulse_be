from rest_framework import serializers

from users.models import User
from .models import Workspace, WorkspaceMember


class WorkspaceSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'address', 'logo', 'overtime_policy', 'created_by', 'created_at', 'member_count']
        read_only_fields = ['id', 'created_by', 'created_at', 'member_count']

    def get_created_by(self, obj):
        user = obj.created_by
        if not user:
            return None
        return {
            'id': str(user.id),
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
        }

    def get_member_count(self, obj):
        return obj.members.count()


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    # Manager field added here (pointing to User ID)
    manager = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = WorkspaceMember
        fields = [
            'id',
            'workspace',
            'user',
            'role',
            'manager',
            'user_email',
            'user_name',
        ]

    def validate(self, attrs):
        workspace = attrs.get("workspace")
        manager = attrs.get("manager")

        # If a manager is selected
        if manager:
            # Check if manager is part of the same workspace
            membership = WorkspaceMember.objects.filter(user=manager, workspace=workspace).first()
            if not membership:
                raise serializers.ValidationError(
                    {"manager": "Manager must belong to the same workspace."}
                )

            # Check role of manager
            if membership.role not in ["manager", "admin"]:
                raise serializers.ValidationError(
                    {"manager": "Selected user is not a manager or admin."}
                )

        return attrs
