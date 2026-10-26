from rest_framework import serializers

from users.models import User
from .models import Crew, Workspace, WorkspaceMember
from .permissions import ADMIN_ONLY, MANAGE_ROLES, has_workspace_role


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
    crew = serializers.PrimaryKeyRelatedField(queryset=Crew.objects.all(), required=False, allow_null=True)
    crew_name = serializers.SerializerMethodField()

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
            'crew',
            'crew_name',
            'wage_rate',
        ]

    def get_crew_name(self, obj):
        return obj.crew.name if obj.crew_id and obj.crew else None

    def _actor_has_role(self, workspace_id, roles):
        """Cached per serializer (a list renders many members of the same workspace)."""
        request = self.context.get("request")
        actor = request.user if request else None
        cache = self.context.setdefault("_workspace_roles", {})
        key = (workspace_id, roles)
        if key not in cache:
            cache[key] = bool(actor and has_workspace_role(actor, workspace_id, roles))
        return cache[key]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # What someone earns is for admins only.
        if not self._actor_has_role(instance.workspace_id, ADMIN_ONLY):
            data.pop("wage_rate", None)
        return data

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

        target = workspace or (self.instance.workspace if self.instance else None)
        if "crew" in attrs and attrs["crew"] is not None and target and attrs["crew"].workspace_id != target.pk:
            raise serializers.ValidationError({"crew": "Crew must belong to the same workspace."})
        if "crew" in attrs and target and not self._actor_has_role(target.pk, MANAGE_ROLES):
            unchanged = bool(self.instance) and attrs["crew"] == self.instance.crew
            if not unchanged:
                raise serializers.ValidationError({"crew": "Only workspace admins and managers can change a crew."})
        if "wage_rate" in attrs and target and not self._actor_has_role(target.pk, ADMIN_ONLY):
            raise serializers.ValidationError({"wage_rate": "Only workspace admins can set wage rates."})
        if attrs.get("wage_rate") is not None and attrs["wage_rate"] < 0:
            raise serializers.ValidationError({"wage_rate": "Wage rate can't be negative."})

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


class CrewSerializer(serializers.ModelSerializer):
    foreman_name = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Crew
        fields = ['id', 'workspace', 'name', 'foreman', 'foreman_name', 'member_count', 'created_at']
        read_only_fields = ['id', 'created_at', 'member_count', 'foreman_name']

    def get_foreman_name(self, obj):
        return obj.foreman.get_full_name() if obj.foreman_id and obj.foreman else None

    def validate(self, attrs):
        workspace = attrs.get("workspace") or (self.instance.workspace if self.instance else None)
        name = (attrs.get("name") or (self.instance.name if self.instance else "")).strip()
        if not name:
            raise serializers.ValidationError({"name": "Give the crew a name."})
        if "name" in attrs:
            attrs["name"] = name
        clash = Crew.objects.filter(workspace=workspace, name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": "A crew with this name already exists."})
        foreman = attrs.get("foreman")
        if foreman and not WorkspaceMember.objects.filter(user=foreman, workspace=workspace).exists():
            raise serializers.ValidationError({"foreman": "The foreman must belong to the same workspace."})
        return attrs
