from rest_framework import serializers
from .models import Tag, TimeEntryTag

class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = '__all__'

class TimeEntryTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeEntryTag
        fields = '__all__'
