from rest_framework import serializers
from .models import Workspace, WorkspaceMember

class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ['id', 'name', 'created_by', 'created_at']

class WorkspaceMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkspaceMember
        fields = ['id', 'workspace', 'user', 'role', 'joined_at']
