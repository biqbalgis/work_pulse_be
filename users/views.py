from rest_framework import status, generics, permissions, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
import logging
import secrets
import string

from core.models import ActivityLog
from core.serializers import (
    LoginSerializer, RegisterSerializer, ChangePasswordSerializer,
    UserSerializer, UserProfileSerializer
)
from core.permissions import IsAdminOrSupervisor
from users.models import User, UserProfile
from users.utils import send_invitation_email, generate_invitation_token
from django.conf import settings

logger = logging.getLogger(__name__)


class CustomTokenObtainPairView(TokenObtainPairView):
    """Custom JWT token view with additional user data."""
    
    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        user = serializer.validated_data['user']
        refresh = RefreshToken.for_user(user)
        
        # Update last login IP
        user.last_login_ip = self.get_client_ip(request)
        user.save()
        
        # Log login activity
        try:
            user_profile = user.profile
            ActivityLog.objects.create(
                user=user_profile,
                action='login',
                description=f'User logged in from {self.get_client_ip(request)}',
                ip_address=self.get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except UserProfile.DoesNotExist:
            logger.warning(f"User {user.email} does not have a profile")
        
        return Response({
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': UserSerializer(user).data
        })
    
    def get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class RegisterView(generics.CreateAPIView):
    """User registration view."""
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # Create user profile (this would typically be done in a signal)
        # For now, we'll create it manually
        try:
            # Get the first organization or create a default one
            from core.models import Organization
            organization = Organization.objects.first()
            if not organization:
                organization = Organization.objects.create(
                    name="Default Organization",
                    created_by=user
                )
            
            UserProfile.objects.create(
                user=user,
                organization=organization,
                role='member'
            )
        except Exception as e:
            logger.error(f"Error creating user profile: {e}")
        
        # Generate tokens
        refresh = RefreshToken.for_user(user)
        
        return Response({
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)


class ChangePasswordView(generics.UpdateAPIView):
    """Change password view."""
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        return self.request.user
    
    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        user = self.get_object()
        user.set_password(serializer.validated_data['new_password'])
        user.save()
        
        # Log password change activity
        try:
            user_profile = user.profile
            ActivityLog.objects.create(
                user=user_profile,
                action='password_changed',
                description='User changed password',
                ip_address=self.get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except UserProfile.DoesNotExist:
            logger.warning(f"User {user.email} does not have a profile")
        
        return Response({'message': 'Password changed successfully'})
    
    def get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def logout_view(request):
    """Logout view."""
    try:
        refresh_token = request.data["refresh_token"]
        token = RefreshToken(refresh_token)
        token.blacklist()
        
        # Log logout activity
        try:
            user_profile = request.user.profile
            ActivityLog.objects.create(
                user=user_profile,
                action='logout',
                description='User logged out',
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except UserProfile.DoesNotExist:
            logger.warning(f"User {request.user.email} does not have a profile")
        
        return Response({'message': 'Logout successful'})
    except Exception as e:
        return Response({'error': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_profile_view(request):
    """Get current user profile."""
    try:
        user_profile = request.user.profile
        serializer = UserProfileSerializer(user_profile)
        return Response(serializer.data)
    except UserProfile.DoesNotExist:
        return Response({'error': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PUT', 'PATCH'])
@permission_classes([permissions.IsAuthenticated])
def update_user_profile_view(request):
    """Update current user profile."""
    try:
        user_profile = request.user.profile
        serializer = UserProfileSerializer(user_profile, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()
            
            # Log profile update activity
            ActivityLog.objects.create(
                user=user_profile,
                action='profile_updated',
                description='User updated profile',
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    except UserProfile.DoesNotExist:
        return Response({'error': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)


def get_client_ip(request):
    """Get client IP address."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


class UserViewSet(viewsets.ModelViewSet):
    """ViewSet for User model."""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter users based on current user's organization."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            return User.objects.none()
        # Get all users in the same organization
        profile_ids = UserProfile.objects.filter(organization=user_profile.organization).values_list('user_id', flat=True)
        return User.objects.filter(id__in=profile_ids)
    
    def perform_create(self, serializer):
        """Create user and user profile."""
        from core.models import Organization
        
        # Get organization from request or current user's profile
        organization = None
        organization_id = self.request.data.get('organization_id')
        user_profile_for_logging = None
        
        if organization_id:
            try:
                organization = Organization.objects.get(id=organization_id)
            except Organization.DoesNotExist:
                raise DRFValidationError("Invalid organization ID provided.")
            # Get user_profile for logging if available
            user_profile_for_logging = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        else:
            # Get current user's organization
            user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
            if not user_profile:
                # Superusers might not have profiles - try to get first organization or raise error
                if getattr(self.request.user, 'is_superuser', False):
                    organization = Organization.objects.first()
                    if not organization:
                        raise DRFValidationError("No organization found. Please create an organization first.")
                else:
                    raise DRFValidationError("User profile not found. Please ensure your account is properly set up with an organization, or provide an organization_id in the request.")
            elif not user_profile.organization:
                raise DRFValidationError("User is not associated with an organization. Please contact an administrator.")
            else:
                organization = user_profile.organization
            user_profile_for_logging = user_profile
        
        # Generate a random password if not provided
        password = self.request.data.get('password', None)
        if not password:
            # Generate a secure random password
            alphabet = string.ascii_letters + string.digits + string.punctuation
            password = ''.join(secrets.choice(alphabet) for i in range(16))
        
        # Create user (serializer handles username automatically)
        user = serializer.save()
        user.set_password(password)
        user.is_active = False  # User must accept invitation before they can log in
        user.is_email_verified = False
        user.save()
        
        # Create user profile
        role = self.request.data.get('role', 'member')
        roles = [role] if role else ['member']
        
        profile_data = {
            'user': user,
            'organization': organization,
            'role': role.lower() if role else 'member',
            'roles': roles,
            'billing_rate': self.request.data.get('billing_rate', 0),
            'billing_type': self.request.data.get('billing_type', 'hourly'),
        }
        
        if self.request.data.get('team_id'):
            from core.models import Team
            try:
                team = Team.objects.get(id=self.request.data['team_id'], organization=organization)
                profile_data['team'] = team
            except Team.DoesNotExist:
                pass
        
        if self.request.data.get('supervisor_id'):
            try:
                supervisor_profile = UserProfile.objects.get(
                    id=self.request.data['supervisor_id'],
                    organization=organization
                )
                profile_data['supervisor'] = supervisor_profile
            except UserProfile.DoesNotExist:
                pass
        
        try:
            user_profile_created = UserProfile.objects.create(**profile_data)
            logger.info(f"Created user profile for user {user.id} in organization {organization.id}")
        except Exception as e:
            logger.error(f"Error creating user profile: {e}")
            raise DRFValidationError(f"Failed to create user profile: {str(e)}")
        
        # Send invitation email
        try:
            uid, token = generate_invitation_token(user)
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            invitation_url = f"{frontend_url}/accept-invitation/{uid}/{token}"
            send_invitation_email(user, invitation_url)
            logger.info(f"Invitation email sent to {user.email}")
        except Exception as e:
            logger.error(f"Error sending invitation email to {user.email}: {e}")
            # Don't fail user creation if email fails, just log it
        
        # Log activity (only if user_profile_for_logging exists)
        if user_profile_for_logging:
            try:
                ActivityLog.objects.create(
                    user=user_profile_for_logging,
                    action='user_created',
                    description=f'Created user {user.get_full_name()}',
                    ip_address=get_client_ip(self.request),
                    user_agent=self.request.META.get('HTTP_USER_AGENT', '')
                )
            except Exception as e:
                logger.error(f"Error logging user creation: {e}")
    
    def perform_update(self, serializer):
        """Update user and optionally update profile."""
        user = serializer.save()
        
        # Update profile if profile fields are provided
        if hasattr(user, 'profile'):
            profile = user.profile
            profile_data = {}
            
            if 'role' in self.request.data:
                role = self.request.data['role']
                profile_data['role'] = role.lower() if role else 'member'
                profile_data['roles'] = [role] if role else ['member']
            
            if 'billing_rate' in self.request.data:
                profile_data['billing_rate'] = self.request.data['billing_rate']
            
            if 'billing_type' in self.request.data:
                profile_data['billing_type'] = self.request.data['billing_type']
            
            if 'team_id' in self.request.data:
                if self.request.data['team_id']:
                    from core.models import Team
                    try:
                        team = Team.objects.get(id=self.request.data['team_id'], organization=profile.organization)
                        profile_data['team'] = team
                    except Team.DoesNotExist:
                        pass
                else:
                    profile_data['team'] = None
            
            if 'supervisor_id' in self.request.data:
                if self.request.data['supervisor_id']:
                    try:
                        supervisor_profile = UserProfile.objects.get(
                            id=self.request.data['supervisor_id'],
                            organization=profile.organization
                        )
                        profile_data['supervisor'] = supervisor_profile
                    except UserProfile.DoesNotExist:
                        pass
                else:
                    profile_data['supervisor'] = None
            
            if profile_data:
                for key, value in profile_data.items():
                    setattr(profile, key, value)
                profile.save()
    
    def get_permissions(self):
        """Admin/Supervisor-only for write operations; authenticated for reads."""
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        # Only admins/supervisors can create/update users
        return [permissions.IsAuthenticated(), IsAdminOrSupervisor()]
    
    @action(detail=False, methods=['post'], permission_classes=[permissions.AllowAny])
    def accept_invitation(self, request):
        """Accept invitation and set password."""
        uid = request.data.get('uid')
        token = request.data.get('token')
        password = request.data.get('password')
        password_confirm = request.data.get('password_confirm')
        
        if not all([uid, token, password, password_confirm]):
            return Response(
                {'error': 'All fields are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if password != password_confirm:
            return Response(
                {'error': 'Passwords do not match.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify token
        try:
            from django.utils.http import urlsafe_base64_decode
            from django.utils.encoding import force_str
            from django.contrib.auth.tokens import default_token_generator
            
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
            
            if not default_token_generator.check_token(user, token):
                return Response(
                    {'error': 'Invalid or expired invitation link.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate password
            try:
                validate_password(password, user)
            except ValidationError as e:
                return Response(
                    {'error': '; '.join(e.messages)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Set password and mark email as verified
            user.set_password(password)
            user.is_email_verified = True
            user.is_active = True
            user.save()
            
            logger.info(f"User {user.email} accepted invitation and set password")
            
            return Response({
                'message': 'Invitation accepted successfully. You can now log in.',
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error accepting invitation: {e}")
            return Response(
                {'error': 'Invalid invitation link.'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def verify_invitation(self, request):
        """Verify invitation token without setting password."""
        uid = request.query_params.get('uid')
        token = request.query_params.get('token')
        
        if not uid or not token:
            return Response(
                {'valid': False, 'error': 'Missing uid or token'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from django.utils.http import urlsafe_base64_decode
            from django.utils.encoding import force_str
            from django.contrib.auth.tokens import default_token_generator
            
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
            
            is_valid = default_token_generator.check_token(user, token)
            
            if is_valid:
                return Response({
                    'valid': True,
                    'user': {
                        'email': user.email,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                    }
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'valid': False,
                    'error': 'Invalid or expired invitation link'
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"Error verifying invitation: {e}")
            return Response({
                'valid': False,
                'error': 'Invalid invitation link'
            }, status=status.HTTP_400_BAD_REQUEST)