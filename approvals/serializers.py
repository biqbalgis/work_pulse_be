from rest_framework import serializers
from .models import TimeEntryApproval, TimeEntryApprovalItem

class TimeEntryApprovalItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntryApprovalItem
        fields = '__all__'

class TimeEntryApprovalSerializer(serializers.ModelSerializer):
    items = TimeEntryApprovalItemSerializer(many=True, read_only=True)
    class Meta:
        model = TimeEntryApproval
        fields = '__all__'
