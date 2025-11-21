from rest_framework import serializers
from .models import TimeEntryApproval, TimeEntryApprovalItem

class TimeEntryApprovalItemSerializer(serializers.ModelSerializer):
    time_entry_data = serializers.SerializerMethodField()

    class Meta:
        model = TimeEntryApprovalItem
        fields = ["id", "time_entry", "approved", "comments", "time_entry_data"]

    def get_time_entry_data(self, obj):
        t = obj.time_entry
        return {
            "duration_hours": t.duration / 60,
            "description": t.description,
            "project": t.project.name if t.project else None,
            "date": t.start_time.date(),
        }

class TimeEntryApprovalSerializer(serializers.ModelSerializer):
    items = TimeEntryApprovalItemSerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = TimeEntryApproval
        fields = [
            "id", "workspace", "user", "user_name",
            "start_date", "end_date", "status",
            "reviewed_by", "notes", "total_hours",
            "items", "created_at"
        ]
        read_only_fields = ["reviewed_by", "total_hours", "status"]
