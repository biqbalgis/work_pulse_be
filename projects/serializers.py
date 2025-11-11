from rest_framework import serializers
from .models import Project

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'workspace', 'client', 'name', 'color', 'is_active', 'billable', 'created_by', 'created_at']
