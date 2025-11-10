from rest_framework import permissions
from users.models import UserProfile


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner of the snippet.
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return obj.user == user_profile


class IsAdminOrSupervisor(permissions.BasePermission):
    """
    Custom permission to only allow admins and supervisors.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, 'is_superuser', False):
            return True
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return user_profile.is_admin or user_profile.is_supervisor


class IsAdminOnly(permissions.BasePermission):
    """
    Custom permission to only allow admins.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, 'is_superuser', False):
            return True
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return user_profile.is_admin


class IsOwnerOrAdminOrSupervisor(permissions.BasePermission):
    """
    Custom permission to allow owners, admins, and supervisors.
    """
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        
        # Admins and supervisors can access everything
        if user_profile.is_admin or user_profile.is_supervisor:
            return True
        
        # Owners can access their own objects
        if hasattr(obj, 'user'):
            return obj.user == user_profile
        
        return False


class CanApproveTimesheets(permissions.BasePermission):
    """
    Custom permission to check if user can approve timesheets.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return user_profile.can_approve_timesheets


class CanManageUsers(permissions.BasePermission):
    """
    Custom permission to check if user can manage users.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return user_profile.can_manage_users


class CanViewReports(permissions.BasePermission):
    """
    Custom permission to check if user can view reports.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        return user_profile.can_view_reports


class IsSameOrganization(permissions.BasePermission):
    """
    Custom permission to ensure users can only access data from their organization.
    """
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        if not user_profile:
            return False
        
        # Check if the object belongs to the same organization
        if hasattr(obj, 'organization'):
            return obj.organization == user_profile.organization
        elif hasattr(obj, 'user'):
            return obj.user.organization == user_profile.organization
        elif hasattr(obj, 'project'):
            return obj.project.organization == user_profile.organization
        
        return False
