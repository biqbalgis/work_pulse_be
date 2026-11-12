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
    # Their permission group (user_permissions.PermissionGroup); changed through /api/user-permissions/.
    permission_group = serializers.SerializerMethodField()
    permission_group_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMember
        fields = [
            'id',
            'workspace',
            'user',
            'role',
            'group',
            'group_label',
            'permission_group',
            'permission_group_name',
            'manager',
            'user_email',
            'user_name',
        ]

    def get_permission_group(self, obj):
        value = getattr(obj, "permission_group_id", None)
        return str(value) if value else None

    def get_permission_group_name(self, obj):
        return getattr(obj, "permission_group_name", None) or ""

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
            from user_permissions.access import has_perm

            may_edit = bool(actor and target_ws and has_perm(actor, target_ws.pk, "team.edit"))
            unchanged = bool(self.instance) and attrs["group"] == self.instance.group
            if not may_edit and not unchanged:
                raise serializers.ValidationError({"group": "You don't have permission to change a user's group."})

        # If a manager is selected
        if manager:
            # Check if manager is part of the same workspace
            membership = WorkspaceMember.objects.filter(user=manager, workspace=workspace).first()
            if not membership:
                raise serializers.ValidationError(
                    {"manager": "Manager must belong to the same workspace."}
                )

            # Anyone in the workspace may be someone's approver (they can then review that person's time),
            # just not the person themselves.
            member_user = attrs.get("user") or (self.instance.user if self.instance else None)
            if member_user is not None and member_user.pk == manager.pk:
                raise serializers.ValidationError({"manager": "Someone can't be their own approver."})

        return attrs

