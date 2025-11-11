from rest_framework import serializers
from .models import TimeOffType, TimeOffRequest

class TimeOffTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeOffType
        fields = '__all__'

class TimeOffRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeOffRequest
        fields = '__all__'
