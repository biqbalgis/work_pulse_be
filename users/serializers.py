from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from workspaces.models import Workspace, WorkspaceMember
from .models import PasswordResetToken, User


def _workspace_logo_url(workspace, request=None):
    """Absolute URL for a workspace's logo, or None if it has none."""
    if not workspace.logo:
        return None
    return request.build_absolute_uri(workspace.logo.url) if request else workspace.logo.url


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

        # 🔍 Find workspace membership
        memberships = WorkspaceMember.objects.filter(user=user).select_related("workspace")
        workspace_data = None
        role = None
        request = self.context.get("request")

        if memberships.exists():
            # Pick the first workspace for now (support multiple later if needed)
            workspace_data = []
            for m in memberships:
                workspace_data.append({
                    "id": str(m.workspace.id),
                    "name": m.workspace.name,
                    "role": m.role,
                    "logo": _workspace_logo_url(m.workspace, request),
                })


        else:
            # Global or superuser (no workspace)
            workspace_data = {
                "id": None,
                "name": None,
                "role": "superuser" if user.is_superuser else None,
                "logo": None,
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
        choices=[('admin', 'Admin'), ('manager', 'Manager'), ('user', 'User'),('field_manager','Field Manager')],
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
        workspace = None
        if workspace_id:
            workspace = Workspace.objects.filter(id=workspace_id).first()

        if not workspace:
            if request_user and request_user.is_superuser:
                user = User.objects.create_user(**validated_data)
                return user
            else:
                raise serializers.ValidationError("Workspace ID or name is required for non-superuser registration.")

        user = User.objects.create_user(**validated_data)
        WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
        return user


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True, required=False)

    INVALID_TOKEN_MESSAGE = "This link is invalid or has expired. Please request a new one."

    def validate(self, attrs):
        confirm_password = attrs.get("confirm_password")
        if confirm_password is not None and confirm_password != attrs["password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        try:
            reset_token = PasswordResetToken.objects.select_related("user").get(token=attrs["token"])
        except PasswordResetToken.DoesNotExist:
            raise serializers.ValidationError({"token": self.INVALID_TOKEN_MESSAGE})

        if not reset_token.is_valid:
            raise serializers.ValidationError({"token": self.INVALID_TOKEN_MESSAGE})

        try:
            validate_password(attrs["password"], user=reset_token.user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages})

        attrs["reset_token"] = reset_token
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        user = self.context["request"].user
        if not user.check_password(attrs["old_password"]):
            raise serializers.ValidationError({"old_password": "Current password is incorrect."})

        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages})

        return attrs
