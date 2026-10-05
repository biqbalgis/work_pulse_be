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

    group = serializers.ChoiceField(
        choices=WorkspaceMember.GROUP_CHOICES, required=False, allow_null=True, allow_blank=True,
    )
    group_label = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMember
        fields = [
            'id',
            'workspace',
            'user',
            'role',
            'group',
            'group_label',
            'manager',
            'user_email',
            'user_name',
        ]

    def get_group_label(self, obj):
        return obj.get_group_display() if obj.group else None

    def validate_group(self, value):
        # An empty value clears the group ("remove from group").
        return value or None

    def validate(self, attrs):
        workspace = attrs.get("workspace")
        manager = attrs.get("manager")

        # Only superusers and admins of the workspace may add/change/remove a user's group.
        if "group" in attrs:
            request = self.context.get("request")
            target_ws = workspace or (self.instance.workspace if self.instance else None)
            actor = request.user if request else None
            is_admin = bool(
                actor and target_ws and (
                    actor.is_superuser
                    or WorkspaceMember.objects.filter(user=actor, workspace=target_ws, role="admin").exists()
                )
            )
            unchanged = bool(self.instance) and attrs["group"] == self.instance.group
            if not is_admin and not unchanged:
                raise serializers.ValidationError({"group": "Only workspace admins can change a user's group."})

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
