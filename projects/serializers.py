from rest_framework import serializers

from workspaces.models import Workspace
from .models import Project, JobTitle, ProjectRole, UserProjectRole, RoleTemplate, RoleTemplateUser


class ProjectSerializer(serializers.ModelSerializer):
    workspace = serializers.PrimaryKeyRelatedField(
        queryset=Workspace.objects.all(),
        required=False  # 👈 IMPORTANT
    )
    workspace_name = serializers.CharField(source='workspace.name', read_only=True)
    client_name = serializers.CharField(source='client.name', read_only=True)

    class Meta:
        model = Project
        fields = "__all__"
        read_only_fields = ["created_by"]  # DO NOT put workspace here

    def validate(self, attrs):
        from workspaces.models import WorkspaceMember

        user = self.context['request'].user

        # Determine workspace — superuser passes it explicitly, others use membership
        if user.is_superuser:
            workspace = attrs.get('workspace') or (self.instance.workspace if self.instance else None)
        else:
            attrs.pop('workspace', None)
            member = WorkspaceMember.objects.filter(user=user).first()
            workspace = member.workspace if member else (self.instance.workspace if self.instance else None)

        # Workspace-scoped job_code uniqueness check
        job_code = attrs.get('job_code')
        if job_code and workspace:
            qs = Project.objects.filter(workspace=workspace, job_code=job_code, is_deleted=False)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    'job_code': f"Job number '{job_code}' is already used in this workspace."
                })

        return attrs

class JobTitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobTitle
        fields = ['id', 'name']


class ProjectRoleSerializer(serializers.ModelSerializer):
    job_title_name = serializers.CharField(source='job_title.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    
    class Meta:
        model = ProjectRole
        fields = ['id', 'project', 'project_name', 'job_title', 'job_title_name', 'hourly_rate']


class UserProjectRoleSerializer(serializers.ModelSerializer):
    user_full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    job_title_name = serializers.CharField(source="job_title.name", read_only=True)
    workspace_name = serializers.CharField(source="project.workspace.name", read_only=True)
    user_uuid = serializers.UUIDField(source="user.id", read_only=True)
    class Meta:
        model = UserProjectRole
        fields = ['id', 'user', 'user_uuid', 'project', 'job_title', 'hourly_rate','user_full_name','project_name','job_title_name', 'workspace_name']



class CurrentUserRoleSerializer(serializers.ModelSerializer):
    job_title_name = serializers.CharField(source='job_title.name', read_only=True)
    
    class Meta:
        model = UserProjectRole
        fields = ['job_title', 'job_title_name', 'hourly_rate']

class AddProjectRoleSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    job_title_name = serializers.CharField(max_length=200)
    hourly_rate = serializers.DecimalField(max_digits=8, decimal_places=2)


class AssignProjectRoleUsersSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    job_title_name = serializers.CharField(max_length=200, trim_whitespace=True)
    hourly_rate = serializers.DecimalField(max_digits=10, decimal_places=2)
    users = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False
    )

    def validate_users(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate users are not allowed.")
        return value


# ── Role Template Serializers ─────────────────────────────────────────────────

class RoleTemplateUserOutputSerializer(serializers.ModelSerializer):
    user_id        = serializers.UUIDField(source='user.id', read_only=True)
    user_full_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_email     = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model  = RoleTemplateUser
        fields = ['user_id', 'user_full_name', 'user_email']


class RoleTemplateOutputSerializer(serializers.ModelSerializer):
    job_title_id   = serializers.UUIDField(source='job_title.id', read_only=True)
    job_title_name = serializers.CharField(source='job_title.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    users          = RoleTemplateUserOutputSerializer(source='template_users', many=True, read_only=True)

    class Meta:
        model  = RoleTemplate
        fields = [
            'id', 'template_name',
            'job_title_id', 'job_title_name', 'hourly_rate',
            'users',
            'created_by', 'created_by_name',
            'created_at', 'updated_at',
        ]


class RoleTemplateCreateSerializer(serializers.Serializer):
    template_name  = serializers.CharField(max_length=255)
    job_title_id   = serializers.UUIDField()
    job_title_name = serializers.CharField(max_length=255)
    hourly_rate    = serializers.DecimalField(max_digits=10, decimal_places=2)
    user_ids       = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        min_length=1,
    )

    def validate_user_ids(self, value):
        if len(value) != len(set(str(v) for v in value)):
            raise serializers.ValidationError("Duplicate user IDs are not allowed.")
        return value
