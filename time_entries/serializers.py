from rest_framework import serializers
from .models import TimeEntry

class TimeEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntry
        fields = ["id","user","workspace","project","task","job_title","description",
            "start_time","end_time","duration","hourly_rate","cost","billable","meals","hotels","created_at","created_by",]
        read_only_fields = ["id","user","workspace","duration","hourly_rate","cost","created_at","created_by",]