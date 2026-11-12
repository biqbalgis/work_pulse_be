from rest_framework import serializers
from .models import TimeOffType, TimeOffRequest

class TimeOffTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeOffType
        fields = '__all__'

class TimeOffRequestSerializer(serializers.ModelSerializer):
    type_name = serializers.CharField(source='type.name', read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)

    class Meta:
        model = TimeOffRequest
        fields = '__all__'
        # Who it's for, where, and the decision are set by the server (approve / reject actions), never sent.
        read_only_fields = ['workspace', 'user', 'status', 'reviewed_by', 'reviewer_comment', 'created_by']
