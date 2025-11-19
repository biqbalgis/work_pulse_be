from rest_framework import serializers
from .models import Project, JobTitle, ProjectRole, UserProjectRole


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            'id',
            'name',
            'client',
            'color',
            'billable',
            'workspace',
            'client_hours_rate',
            'default_rt_hours',
            'created_by',
            'created_at'
        ]
        read_only_fields = ['workspace', 'created_by', 'created_at']

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
    class Meta:
        model = UserProjectRole
        fields = ['id', 'user', 'project', 'job_title', 'hourly_rate']


class AddProjectRoleSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    job_title_name = serializers.CharField(max_length=200)
    hourly_rate = serializers.DecimalField(max_digits=8, decimal_places=2)
