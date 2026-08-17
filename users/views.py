from rest_framework import viewsets, permissions, serializers, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
from rest_framework_simplejwt.views import TokenObtainPairView

from rest_framework.views import APIView

from workspaces.models import WorkspaceMember, Workspace
from .emails import send_password_changed_email, send_password_reset_email
from .models import PasswordResetToken
from .serializers import (
    ChangePasswordSerializer,
    EmailTokenObtainPairSerializer,
    ForgotPasswordSerializer,
    RegisterSerializer,
    ResetPasswordConfirmSerializer,
    UserSerializer,
    _workspace_logo_url,
)
from workspaces.permissions import IsWorkspaceAdmin, IsSuperUser
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity, log_error

User = get_user_model()


def _blacklist_all_tokens_for(user, request=None):
    """Force logout everywhere by blacklisting every outstanding JWT for this user."""
    try:
        tokens = OutstandingToken.objects.filter(user=user)
        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)
    except Exception as e:
        log_error(request, e, {"context": f"Error blacklisting tokens for {user}: {e}"})


class MeView(APIView):
    """
    GET /api/me/

    Returns the logged-in user's profile plus their workspace(s) — same
    shape as the "user" object in the /login/ response, including each
    workspace's logo URL. Usable with just the JWT access token, so the
    frontend can re-fetch this (e.g. on page reload) without logging in
    again.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = WorkspaceMember.objects.filter(user=user).select_related("workspace")

        if memberships.exists():
            workspace_data = [
                {
                    "id": str(m.workspace.id),
                    "name": m.workspace.name,
                    "role": m.role,
                    "logo": _workspace_logo_url(m.workspace, request),
                }
                for m in memberships
            ]
        else:
            workspace_data = {
                "id": None,
                "name": None,
                "role": "superuser" if user.is_superuser else None,
                "logo": None,
            }

        return Response({
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_superuser": user.is_superuser,
            "workspace": workspace_data,
        })

class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin | IsSuperUser]
    lookup_field = 'id'

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('pagination') == 'false':
            return None
        return super().paginate_queryset(queryset)

    def get_queryset(self):
        user = self.request.user
        # Base queryset: only active users and not deleted
        queryset = User.objects.filter(is_active=True, is_deleted=False)

        if user.is_superuser:
            workspace_id = self.request.query_params.get('workspace')
            if not workspace_id:
                raise serializers.ValidationError({"workspace": "Workspace parameter is required for superusers."})
            
            # Filter users belonging to this workspace
            member_ids = WorkspaceMember.objects.filter(workspace_id=workspace_id).values_list('user_id', flat=True)
            queryset = queryset.filter(id__in=member_ids)
        else:
            workspace_ids = get_user_workspace_ids(user)
            member_ids = WorkspaceMember.objects.filter(
                workspace_id__in=workspace_ids
            ).values_list('user_id', flat=True)
            queryset = queryset.filter(id__in=member_ids)
            
            # Optional: allow further filtering
            workspace_id = self.request.query_params.get('workspace')
            if workspace_id:
                 # Ensure user belongs to this workspace
                 if str(workspace_id) in [str(wid) for wid in workspace_ids]:
                     member_ids = WorkspaceMember.objects.filter(workspace_id=workspace_id).values_list('user_id', flat=True)
                     queryset = queryset.filter(id__in=member_ids)

        return queryset

    def perform_create(self, serializer):
        creator = self.request.user

        # Determine workspace context
        workspace = None
        if creator.is_superuser:
            # Superuser can manually assign workspace via payload

            workspace_id = self.request.data.get('workspace')
            if workspace_id:
                workspace = Workspace.objects.filter(id=workspace_id).first()
            else:
                raise serializers.ValidationError("Workspace is required for superuser.")
        else:
            # Regular users use their own workspace context
            workspace = get_user_primary_workspace(creator)

        # Create new user
        email = serializer.validated_data.get("email").strip().lower()
        serializer.validated_data["username"] = email

        new_user = serializer.save(is_active=True, primary_workspace=workspace)

        # Add to workspace as 'user' (default)
        WorkspaceMember.objects.create(
            workspace=workspace,
            user=new_user,
            role='user'
        )

        # Log activity
        log_activity(creator, "CREATE", "User", new_user.id, request=self.request)

    # ✅ Endpoint to deactivate or activate a user
    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()

        status_str = "activated" if user.is_active else "deactivated"
        log_activity(request.user, status_str.upper(), "User", user.id, request=self.request)

        # 🚫 Blacklist all JWT tokens for this user
        if not user.is_active:
            try:
                tokens = OutstandingToken.objects.filter(user=user)
                for token in tokens:
                    BlacklistedToken.objects.get_or_create(token=token)
                tokens.delete()
            except Exception as e:
                log_error(self.request, e, {"context": f"Error while blacklisting tokens for {user}: {e}"})
                print(f"Error while blacklisting tokens for {user}: {e}")

        return Response({"status": f"User has been {status_str}."}, status=status.HTTP_200_OK)


    def perform_update(self, serializer):
        # Ensure username always matches email
        email = serializer.validated_data.get("email")
        if email:
            serializer.validated_data["username"] = email.lower()

        instance = serializer.save()
        log_activity(self.request.user, "UPDATE", "User", instance.id, request=self.request)


class EmailLoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]  # superuser/admins can create

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        new_user = serializer.save()
        if user:
            log_activity(user, "CREATE", "User", new_user.id, request=self.request)
        else:
            log_activity(new_user, "REGISTER", "User", new_user.id, request=self.request)


class ForgotPasswordThrottle(AnonRateThrottle):
    scope = "forgot_password"


class ForgotPasswordView(APIView):
    """
    POST /api/auth/forgot-password/  { "email": "..." }

    Always returns the same generic response whether or not the email
    matches an account, so this endpoint can't be used to enumerate
    registered users. Throttled per-IP so it can't be used to spam
    someone's inbox with reset emails.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ForgotPasswordThrottle]
    GENERIC_MESSAGE = "If an account exists for that email, we've sent password reset instructions."

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()

        user = User.objects.filter(email__iexact=email, is_active=True, is_deleted=False).first()
        if user:
            ttl_minutes = settings.PASSWORD_RESET_TIMEOUT_MINUTES
            reset_token = PasswordResetToken.issue_for_user(user, ttl_minutes=ttl_minutes)
            reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={reset_token.token}"

            try:
                send_password_reset_email(user, reset_url, ttl_minutes)
                log_activity(user, "PASSWORD_RESET_REQUEST", "User", user.id, request=request)
            except Exception as e:
                log_error(request, e, {"context": f"Failed to send password reset email to {email}"})

        return Response({"message": self.GENERIC_MESSAGE}, status=status.HTTP_200_OK)


class ResetPasswordConfirmView(APIView):
    """POST /api/auth/reset-password/  { "token": "...", "password": "...", "confirm_password": "..." }"""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reset_token = serializer.validated_data["reset_token"]
        user = reset_token.user

        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])

        reset_token.used_at = timezone.now()
        reset_token.save(update_fields=["used_at"])

        _blacklist_all_tokens_for(user, request=request)
        log_activity(user, "PASSWORD_RESET", "User", user.id, request=request)

        try:
            send_password_changed_email(user)
        except Exception as e:
            log_error(request, e, {"context": f"Failed to send password-changed email to {user.email}"})

        return Response(
            {"message": "Your password has been reset. You can now log in with your new password."},
            status=status.HTTP_200_OK,
        )


class ChangePasswordView(APIView):
    """PUT /api/auth/change-password/  { old_password, new_password, confirm_password } — requires auth."""

    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        log_activity(user, "PASSWORD_CHANGE", "User", user.id, request=request)

        try:
            send_password_changed_email(user)
        except Exception as e:
            log_error(request, e, {"context": f"Failed to send password-changed email to {user.email}"})

        return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)