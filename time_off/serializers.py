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
