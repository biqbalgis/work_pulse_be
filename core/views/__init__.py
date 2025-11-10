from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from datetime import datetime
import logging

from core.models import (
    Organization, Client, Team, Project, Task, TimeEntry, 
    LeaveRequest, Expense, ActivityLog, Kiosk, KioskSession
)
from core.serializers import (
    OrganizationSerializer, ClientSerializer, TeamSerializer, ProjectSerializer,
    TaskSerializer, TimeEntrySerializer, LeaveRequestSerializer, ExpenseSerializer,
    ActivityLogSerializer, KioskSerializer, KioskSessionSerializer, UserProfileSerializer
)
from users.models import UserProfile
from core.permissions import IsAdminOnly

logger = logging.getLogger(__name__)


class OrganizationViewSet(viewsets.ModelViewSet):
    """ViewSet for Organization model."""
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Filter organizations based on user's role."""
        if getattr(self.request.user, 'is_superuser', False):
            return Organization.objects.all()
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            return Organization.objects.none()
        if user_profile.is_admin or user_profile.is_supervisor:
            return Organization.objects.all()
        return Organization.objects.filter(id=user_profile.organization.id)

    def get_permissions(self):
        """Admin-only for write operations; authenticated for reads."""
        if self.request.method in permissions.SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminOnly()]


class ClientViewSet(viewsets.ModelViewSet):
    """ViewSet for Client model."""
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['organization', 'is_active']
    search_fields = ['name', 'contact_person', 'email']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Filter clients based on user's organization."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            return Client.objects.none()
        return Client.objects.filter(organization=user_profile.organization)

    def perform_create(self, serializer):
        """Automatically set organization from user's profile."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            raise ValidationError("User profile not found. Please ensure your account is properly set up with an organization.")
        if not user_profile.organization:
            raise ValidationError("User is not associated with an organization. Please contact an administrator.")
        serializer.save(organization=user_profile.organization)

    def get_permissions(self):
        """Admin-only for write operations; authenticated for reads."""
        if self.request.method in permissions.SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdminOnly()]


class TeamViewSet(viewsets.ModelViewSet):
    """ViewSet for Team model."""
    queryset = Team.objects.all()
    serializer_class = TeamSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['organization', 'is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Filter teams based on user's organization."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            return Team.objects.none()
        return Team.objects.filter(organization=user_profile.organization)

    def perform_create(self, serializer):
        """Automatically set organization from user's profile."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        if not user_profile:
            raise ValidationError("User profile not found. Please ensure your account is properly set up with an organization.")
        if not user_profile.organization:
            raise ValidationError("User is not associated with an organization. Please contact an administrator.")
        serializer.save(organization=user_profile.organization)

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        """Get team members."""
        team = self.get_object()
        members = team.members.filter(is_active=True)
        serializer = UserProfileSerializer(members, many=True)
        return Response(serializer.data)


class ProjectViewSet(viewsets.ModelViewSet):
    """ViewSet for Project model."""
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['organization', 'client', 'status', 'is_billable']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at', 'start_date']
    ordering = ['name']

    def get_queryset(self):
        """Filter projects based on user's organization."""
        user_profile = self.request.user.profile
        return Project.objects.filter(organization=user_profile.organization)

    @action(detail=True, methods=['get'])
    def time_entries(self, request, pk=None):
        """Get project time entries."""
        project = self.get_object()
        time_entries = project.time_entries.all()
        serializer = TimeEntrySerializer(time_entries, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def tasks(self, request, pk=None):
        """Get project tasks."""
        project = self.get_object()
        tasks = project.tasks.all()
        serializer = TaskSerializer(tasks, many=True)
        return Response(serializer.data)


class TaskViewSet(viewsets.ModelViewSet):
    """ViewSet for Task model."""
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['project', 'assigned_to', 'status', 'priority']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at', 'start_date', 'end_date']
    ordering = ['-created_at']

    def get_queryset(self):
        """Filter tasks based on user's organization."""
        user_profile = self.request.user.profile
        return Task.objects.filter(project__organization=user_profile.organization)

    @action(detail=True, methods=['get'])
    def time_entries(self, request, pk=None):
        """Get task time entries."""
        task = self.get_object()
        time_entries = task.time_entries.all()
        serializer = TimeEntrySerializer(time_entries, many=True)
        return Response(serializer.data)


class TimeEntryViewSet(viewsets.ModelViewSet):
    """ViewSet for TimeEntry model."""
    queryset = TimeEntry.objects.all()
    serializer_class = TimeEntrySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['user', 'project', 'task', 'is_billable', 'is_manual', 'is_active']
    search_fields = ['description']
    ordering_fields = ['start_time', 'created_at']
    ordering = ['-start_time']

    def get_queryset(self):
        """Filter time entries based on user's role and organization."""
        user_profile = self.request.user.profile
        
        if user_profile.is_admin or user_profile.is_supervisor:
            # Admins and supervisors can see all time entries in their organization
            return TimeEntry.objects.filter(project__organization=user_profile.organization)
        else:
            # Regular users can only see their own time entries
            return TimeEntry.objects.filter(user=user_profile)

    def perform_create(self, serializer):
        """Set the user when creating a time entry."""
        serializer.save(user=self.request.user.profile)

    @action(detail=False, methods=['post'])
    def start_timer(self, request):
        """Start a new timer."""
        user_profile = request.user.profile
        
        # Check if user already has an active timer
        active_timer = user_profile.get_current_timer()
        if active_timer:
            return Response(
                {'error': 'You already have an active timer.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            # Set timer as active and without end_time
            serializer.save(
                user=user_profile,
                is_active=True,
                end_time=None
            )
            
            # Log activity
            ActivityLog.objects.create(
                user=user_profile,
                action='timer_start',
                description=f'Started timer for {serializer.instance.project.name}',
                metadata={'project_id': str(serializer.instance.project.id)}
            )
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def stop_timer(self, request, pk=None):
        """Stop an active timer."""
        time_entry = self.get_object()
        
        if not time_entry.is_active or time_entry.end_time:
            return Response(
                {'error': 'Timer is not active.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        time_entry.end_time = timezone.now()
        time_entry.is_active = False
        time_entry.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=time_entry.user,
            action='timer_stop',
            description=f'Stopped timer for {time_entry.project.name}',
            metadata={
                'project_id': str(time_entry.project.id),
                'duration': float(time_entry.duration or 0)
            }
        )
        
        serializer = self.get_serializer(time_entry)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def current_timer(self, request):
        """Get current active timer for the user."""
        user_profile = request.user.profile
        current_timer = user_profile.get_current_timer()
        
        if current_timer:
            serializer = self.get_serializer(current_timer)
            return Response(serializer.data)
        return Response({'message': 'No active timer'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'])
    def timesheet(self, request):
        """Get timesheet data for a specific period."""
        user_profile = request.user.profile
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if not start_date or not end_date:
            return Response(
                {'error': 'start_date and end_date are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Invalid date format. Use YYYY-MM-DD'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get time entries for the period
        time_entries = TimeEntry.objects.filter(
            user=user_profile,
            start_time__date__gte=start_date,
            start_time__date__lte=end_date,
            end_time__isnull=False
        ).order_by('start_time')
        
        serializer = self.get_serializer(time_entries, many=True)
        return Response(serializer.data)


class LeaveRequestViewSet(viewsets.ModelViewSet):
    """ViewSet for LeaveRequest model."""
    queryset = LeaveRequest.objects.all()
    serializer_class = LeaveRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['user', 'leave_type', 'status', 'user__team']
    search_fields = ['reason', 'user__user__first_name', 'user__user__last_name', 'user__user__email']
    ordering_fields = ['start_date', 'created_at', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        """Filter leave requests based on user's role."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        
        if not user_profile:
            return LeaveRequest.objects.none()
        
        queryset = LeaveRequest.objects.all()
        
        # Apply filters from query parameters
        team_id = self.request.query_params.get('team', None)
        date_from = self.request.query_params.get('date_from', None)
        date_to = self.request.query_params.get('date_to', None)
        
        if team_id:
            queryset = queryset.filter(user__team_id=team_id)
        
        if date_from:
            queryset = queryset.filter(end_date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(start_date__lte=date_to)
        
        # Filter based on user role
        if user_profile.is_admin or user_profile.is_supervisor:
            # Admins and supervisors can see all leave requests in their organization
            queryset = queryset.filter(user__organization=user_profile.organization)
        else:
            # Regular users can only see their own leave requests
            queryset = queryset.filter(user=user_profile)
        
        return queryset.select_related('user__user', 'user__team', 'approved_by__user')

    def perform_create(self, serializer):
        """Set the user when creating a leave request."""
        user_profile = getattr(self.request.user, 'profile', None) or UserProfile.objects.filter(user=self.request.user).first()
        
        if not user_profile:
            # Try to create a profile if user is a superuser (use first organization)
            if getattr(self.request.user, 'is_superuser', False):
                from core.models import Organization
                organization = Organization.objects.first()
                if organization:
                    user_profile = UserProfile.objects.create(
                        user=self.request.user,
                        organization=organization,
                        role='admin',
                        roles=['admin']
                    )
                    logger.info(f"Auto-created profile for superuser {self.request.user.email}")
                else:
                    raise ValidationError(
                        "User profile not found and no organization exists. "
                        "Please contact an administrator to set up your account with an organization."
                    )
            else:
                raise ValidationError(
                    "User profile not found. Please ensure your account is properly set up with an organization. "
                    "Contact an administrator if you need help."
                )
        
        if not user_profile.organization:
            raise ValidationError(
                "User is not associated with an organization. Please contact an administrator."
            )
        
        serializer.save(user=user_profile)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a leave request."""
        leave_request = self.get_object()
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        
        if not user_profile:
            return Response(
                {'error': 'User profile not found.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not user_profile.can_approve_timesheets:
            return Response(
                {'error': 'You do not have permission to approve leave requests.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if leave_request.status != 'pending':
            return Response(
                {'error': 'Leave request is not pending.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        leave_request.status = 'approved'
        leave_request.approved_by = user_profile
        leave_request.approved_at = timezone.now()
        leave_request.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=user_profile,
            action='leave_approved',
            description=f'Approved leave request for {leave_request.user.user.get_full_name()}',
            metadata={'leave_request_id': str(leave_request.id)}
        )
        
        serializer = self.get_serializer(leave_request)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a leave request."""
        leave_request = self.get_object()
        user_profile = getattr(request.user, 'profile', None) or UserProfile.objects.filter(user=request.user).first()
        
        if not user_profile:
            return Response(
                {'error': 'User profile not found.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not user_profile.can_approve_timesheets:
            return Response(
                {'error': 'You do not have permission to reject leave requests.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if leave_request.status != 'pending':
            return Response(
                {'error': 'Leave request is not pending.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rejection_reason = request.data.get('rejection_reason', '')
        
        leave_request.status = 'rejected'
        leave_request.approved_by = user_profile
        leave_request.approved_at = timezone.now()
        leave_request.rejection_reason = rejection_reason
        leave_request.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=user_profile,
            action='leave_rejected',
            description=f'Rejected leave request for {leave_request.user.user.get_full_name()}',
            metadata={
                'leave_request_id': str(leave_request.id),
                'rejection_reason': rejection_reason
            }
        )
        
        serializer = self.get_serializer(leave_request)
        return Response(serializer.data)


class ExpenseViewSet(viewsets.ModelViewSet):
    """ViewSet for Expense model."""
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['project', 'user', 'category', 'status']
    search_fields = ['description']
    ordering_fields = ['date', 'amount', 'created_at']
    ordering = ['-date']

    def get_queryset(self):
        """Filter expenses based on user's role."""
        user_profile = self.request.user.profile
        
        if user_profile.is_admin or user_profile.is_supervisor:
            # Admins and supervisors can see all expenses in their organization
            return Expense.objects.filter(project__organization=user_profile.organization)
        else:
            # Regular users can only see their own expenses
            return Expense.objects.filter(user=user_profile)

    def perform_create(self, serializer):
        """Set the user when creating an expense."""
        serializer.save(user=self.request.user.profile)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve an expense."""
        expense = self.get_object()
        user_profile = request.user.profile
        
        if not user_profile.can_approve_timesheets:
            return Response(
                {'error': 'You do not have permission to approve expenses.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if expense.status != 'pending':
            return Response(
                {'error': 'Expense is not pending.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        expense.status = 'approved'
        expense.approved_by = user_profile
        expense.approved_at = timezone.now()
        expense.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=user_profile,
            action='expense_approved',
            description=f'Approved expense for {expense.user.user.get_full_name()}',
            metadata={'expense_id': str(expense.id)}
        )
        
        serializer = self.get_serializer(expense)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject an expense."""
        expense = self.get_object()
        user_profile = request.user.profile
        
        if not user_profile.can_approve_timesheets:
            return Response(
                {'error': 'You do not have permission to reject expenses.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if expense.status != 'pending':
            return Response(
                {'error': 'Expense is not pending.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rejection_reason = request.data.get('rejection_reason', '')
        
        expense.status = 'rejected'
        expense.approved_by = user_profile
        expense.approved_at = timezone.now()
        expense.rejection_reason = rejection_reason
        expense.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=user_profile,
            action='expense_rejected',
            description=f'Rejected expense for {expense.user.user.get_full_name()}',
            metadata={
                'expense_id': str(expense.id),
                'rejection_reason': rejection_reason
            }
        )
        
        serializer = self.get_serializer(expense)
        return Response(serializer.data)


class ActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for ActivityLog model (read-only)."""
    queryset = ActivityLog.objects.all()
    serializer_class = ActivityLogSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['user', 'action']
    search_fields = ['description']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        """Filter activity logs based on user's role."""
        user_profile = self.request.user.profile
        
        if user_profile.is_admin or user_profile.is_supervisor:
            # Admins and supervisors can see all activity logs in their organization
            return ActivityLog.objects.filter(user__organization=user_profile.organization)
        else:
            # Regular users can only see their own activity logs
            return ActivityLog.objects.filter(user=user_profile)


class KioskViewSet(viewsets.ModelViewSet):
    """ViewSet for Kiosk model."""
    queryset = Kiosk.objects.all()
    serializer_class = KioskSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['organization', 'is_active']
    search_fields = ['name', 'location']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Filter kiosks based on user's organization."""
        user_profile = self.request.user.profile
        return Kiosk.objects.filter(organization=user_profile.organization)

    @action(detail=True, methods=['post'])
    def check_in(self, request, pk=None):
        """Check in a user at a kiosk."""
        kiosk = self.get_object()
        user_profile = request.user.profile
        
        # Check if user already has an active session
        active_session = KioskSession.objects.filter(
            user=user_profile,
            is_active=True
        ).first()
        
        if active_session:
            return Response(
                {'error': 'You already have an active kiosk session.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create new session
        session = KioskSession.objects.create(
            kiosk=kiosk,
            user=user_profile,
            check_in_time=timezone.now(),
            is_active=True
        )
        
        serializer = KioskSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def check_out(self, request, pk=None):
        """Check out a user from a kiosk."""
        kiosk = self.get_object()
        user_profile = request.user.profile
        
        # Find active session
        active_session = KioskSession.objects.filter(
            kiosk=kiosk,
            user=user_profile,
            is_active=True
        ).first()
        
        if not active_session:
            return Response(
                {'error': 'No active session found.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # End session
        active_session.check_out_time = timezone.now()
        active_session.is_active = False
        active_session.save()
        
        serializer = KioskSessionSerializer(active_session)
        return Response(serializer.data)


class KioskSessionViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for KioskSession model (read-only)."""
    queryset = KioskSession.objects.all()
    serializer_class = KioskSessionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['kiosk', 'user', 'is_active']
    ordering_fields = ['check_in_time', 'created_at']
    ordering = ['-check_in_time']

    def get_queryset(self):
        """Filter kiosk sessions based on user's role."""
        user_profile = self.request.user.profile
        
        if user_profile.is_admin or user_profile.is_supervisor:
            # Admins and supervisors can see all sessions in their organization
            return KioskSession.objects.filter(kiosk__organization=user_profile.organization)
        else:
            # Regular users can only see their own sessions
            return KioskSession.objects.filter(user=user_profile)
