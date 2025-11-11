from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from workspaces.models import Workspace
from .models import User

class UserSerializer(serializers.ModelSerializer):
    workspace = serializers.PrimaryKeyRelatedField(
        queryset=Workspace.objects.all(),
        write_only=True,
        required=False
    )

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "is_active", "workspace"]
        read_only_fields = ["id", "is_active"]

class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'email'

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        if email:
            email = email.strip().lower()

        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is deactivated.")

        # Get primary workspace info
        workspace = user.primary_workspace
        role = None
        if workspace:
            member = WorkspaceMember.objects.filter(user=user, workspace=workspace).first()
            role = member.role if member else None

        refresh = self.get_token(user)
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {
                'id': str(user.id),  # in case of UUIDs
                'email': user.email,
                # 'first_name': user.first_name,
                # 'last_name': user.last_name,
                'primary_workspace': {
                    'id': str(workspace.id) if workspace else None,
                    'name': workspace.name if workspace else None,
                    'role': role,
                }
            },
        }
        return data