from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from workspaces.models import Workspace, WorkspaceMember
from .models import User

class UserSerializer(serializers.ModelSerializer):
    workspace = serializers.PrimaryKeyRelatedField(
        queryset=Workspace.objects.all(),
        write_only=True,
        required=False
    )

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "is_active"]
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

        # 🔍 Find workspace membership
        memberships = WorkspaceMember.objects.filter(user=user)
        workspace_data = None
        role = None

        if memberships.exists():
            # Pick the first workspace for now (support multiple later if needed)
            workspace_data = []
            for m in memberships:
                workspace_data.append({
                    "id": str(m.workspace.id),
                    "name": m.workspace.name,
                    "role": m.role
                })


        else:
            # Global or superuser (no workspace)
            workspace_data = {
                "id": None,
                "name": None,
                "role": "superuser" if user.is_superuser else None,
            }

        # ✅ Generate JWT tokens
        refresh = self.get_token(user)

        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {
                'id': str(user.id),
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_superuser': user.is_superuser,
                'workspace': workspace_data,
            },
        }
        return data


class RegisterSerializer(serializers.ModelSerializer):
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
    role = serializers.ChoiceField(
        choices=[('admin', 'Admin'), ('manager', 'Manager'), ('user', 'User')],
        default='user'
    )

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'password',
            'workspace_id', 'role'
        ]
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        workspace_id = validated_data.pop('workspace_id', None)
        role = validated_data.pop('role', 'user')

        email = validated_data.get('email').strip().lower()
        validated_data['username'] = email

        request_user = self.context['request'].user if self.context.get('request') else None

        # 🧩 CASE 1: Superuser creating a user
        if request_user and request_user.is_superuser:
            # If workspace_id is provided, create user with workspace
            # if workspace_id:
            #     workspace = Workspace.objects.filter(id=workspace_id).first()
            #     if workspace:
            #         user = User.objects.create_user(**validated_data, primary_workspace=workspace)
            #         WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
            #         return user
            # # Otherwise, create global user (no workspace link)
            user = User.objects.create_user(**validated_data)
            return user

        # 🧩 CASE 2: Workspace Admin / Manager creating user within a workspace
        workspace = None
        if workspace_id:
            workspace = Workspace.objects.filter(id=workspace_id).first()

        if not workspace:
            raise serializers.ValidationError("Workspace ID or name is required for non-superuser registration.")

        user = User.objects.create_user(**validated_data, primary_workspace=workspace)
        WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
        return user
