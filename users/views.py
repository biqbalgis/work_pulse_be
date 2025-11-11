from rest_framework import viewsets, permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

from workspaces.models import WorkspaceMember
from .serializers import UserSerializer
from workspaces.permissions import IsWorkspaceManager, IsSuperUser
from core.utils.workspace_utils import get_user_workspace_ids, get_user_primary_workspace
from core.utils.logger import log_activity, log_error

User = get_user_model()

class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceManager | IsSuperUser]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return User.objects.all()
        workspace_ids = get_user_workspace_ids(user)
        member_ids = WorkspaceMember.objects.filter(workspace_id__in=workspace_ids).values_list('user_id', flat=True)
        return User.objects.filter(id__in=member_ids)

    def perform_create(self, serializer):
        user = self.request.user
        workspace = get_user_primary_workspace(user)
        new_user = serializer.save(is_active=True)
        WorkspaceMember.objects.create(workspace=workspace, user=new_user, role='user')
        log_activity(user, "CREATE", "User", new_user.id,request=self.request)

    # ✅ New endpoint to deactivate or activate a user
    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()

        status_str = "activated" if user.is_active else "deactivated"
        log_activity(request.user, status_str.upper(), "User", user.id,request=self.request)

        # 🚫 Blacklist all JWT tokens for this user
        if not user.is_active:
            try:
                tokens = OutstandingToken.objects.filter(user=user)
                for token in tokens:
                    BlacklistedToken.objects.get_or_create(token=token)
                tokens.delete()  # Remove all OutstandingToken records
            except Exception as e:
                log_error(self.request, e, {"context": f"Error while blacklisting tokens for {user}: {e}"})
                print(f"Error while blacklisting tokens for {user}: {e}")

        return Response(
            {"status": f"User has been {status_str}."},
            status=status.HTTP_200_OK
        )
