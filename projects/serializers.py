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
            'created_by',
            'created_at'
        ]
        read_only_fields = ['workspace', 'created_by', 'created_at']

class JobTitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobTitle
        fields = ['id', 'name']


class ProjectRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectRole
        fields = ['id', 'project', 'job_title', 'hourly_rate']


class UserProjectRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProjectRole
        fields = ['id', 'user', 'project', 'job_title', 'hourly_rate']