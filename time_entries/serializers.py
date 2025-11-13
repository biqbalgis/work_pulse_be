from rest_framework import serializers
from .models import TimeEntry

class TimeEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntry
        fields = [
            'id', 'user', 'workspace', 'project', 'task',
            'job_title', 'description', 'start_time', 'end_time',
            'duration', 'hourly_rate', 'cost', 'billable'
        ]
        read_only_fields = ['user', 'hourly_rate', 'cost', 'duration']