from rest_framework import serializers
from .models import Workspace, WorkspaceMember


class WorkspaceSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'created_by', 'created_at', 'member_count']
        read_only_fields = ['id', 'created_by', 'created_at', 'member_count']

    def get_created_by(self, obj):
        user = obj.created_by
        if not user:
            return None
        return {
            'id': str(user.id),
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
        }

    def get_member_count(self, obj):
        return obj.members.count()


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = WorkspaceMember
        fields = [
            'id',
            'workspace',
            'user',
            'role',
            'user_email',
            'user_name',
        ]