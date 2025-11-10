from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from core.models import (
    Organization, Client, Team, Project, Task, TimeEntry, 
    LeaveRequest, Expense, ActivityLog, Kiosk, KioskSession
)
from users.models import User, UserProfile


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    is_superuser = serializers.BooleanField(read_only=True)
    roles = serializers.SerializerMethodField()
    username = serializers.CharField(required=False, allow_blank=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name', 'phone', 'avatar', 'is_active', 'is_superuser', 'roles', 'date_joined']
        read_only_fields = ['id', 'date_joined', 'is_superuser', 'roles']
        extra_kwargs = {
            'username': {'required': False},
        }
    
    def validate(self, data):
        """Set username to email if not provided."""
        if 'username' not in data or not data.get('username'):
            if 'email' in data and data.get('email'):
                data['username'] = data['email']
        return data
    
    def create(self, validated_data):
        """Create user with username set to email if not provided."""
        # Ensure username is set to email if not provided
        if 'username' not in validated_data or not validated_data.get('username'):
            validated_data['username'] = validated_data.get('email', '')
        return super().create(validated_data)
    
    def get_roles(self, obj):
        """Get user roles from profile if it exists."""
        try:
            profile = obj.profile
            if profile.roles and isinstance(profile.roles, list) and len(profile.roles) > 0:
                return profile.roles
            # Backward compatibility: return single role as list
            return [profile.role] if profile.role else ['member']
        except UserProfile.DoesNotExist:
            return []


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for UserProfile model."""
    user = UserSerializer(read_only=True)
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)
    supervisor_name = serializers.CharField(source='supervisor.user.get_full_name', read_only=True)
    subordinates_count = serializers.SerializerMethodField()
    current_timer = serializers.SerializerMethodField()
    today_hours = serializers.SerializerMethodField()
    week_hours = serializers.SerializerMethodField()
    utilization_percentage = serializers.SerializerMethodField()
    
    class Meta:
        model = UserProfile
        fields = [
            'id', 'user', 'organization', 'organization_name', 'team', 'team_name',
            'supervisor', 'supervisor_name', 'role', 'roles', 'billing_rate', 'billing_type',
            'daily_capacity', 'working_days', 'week_start', 'timezone', 'is_active',
            'hire_date', 'termination_date', 'subordinates_count', 'current_timer',
            'today_hours', 'week_hours', 'utilization_percentage'
        ]
        read_only_fields = ['id', 'subordinates_count', 'current_timer', 'today_hours', 'week_hours', 'utilization_percentage']
    
    def get_subordinates_count(self, obj):
        return obj.get_subordinates().count()
    
    def get_current_timer(self, obj):
        timer = obj.get_current_timer()
        if timer:
            return TimeEntrySerializer(timer).data
        return None
    
    def get_today_hours(self, obj):
        return float(obj.get_today_hours())
    
    def get_week_hours(self, obj):
        return float(obj.get_week_hours())
    
    def get_utilization_percentage(self, obj):
        return round(obj.get_utilization_percentage(), 2)


class OrganizationSerializer(serializers.ModelSerializer):
    """Serializer for Organization model."""
    clients_count = serializers.SerializerMethodField()
    teams_count = serializers.SerializerMethodField()
    users_count = serializers.SerializerMethodField()
    projects_count = serializers.SerializerMethodField()
    today_cost = serializers.SerializerMethodField()
    week_cost = serializers.SerializerMethodField()
    month_cost = serializers.SerializerMethodField()
    
    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'description', 'address', 'phone', 'email', 'website',
            'timezone', 'working_days', 'public_holidays', 'is_active', 'created_by',
            'clients_count', 'teams_count', 'users_count', 'projects_count',
            'today_cost', 'week_cost', 'month_cost',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'clients_count', 'teams_count', 'users_count', 'projects_count', 'today_cost', 'week_cost', 'month_cost']
    
    def get_clients_count(self, obj):
        return obj.clients.filter(is_active=True).count()
    
    def get_teams_count(self, obj):
        return obj.teams.filter(is_active=True).count()
    
    def get_users_count(self, obj):
        return obj.user_profiles.filter(is_active=True).count()
    
    def get_projects_count(self, obj):
        return obj.projects.filter(status='active').count()
    
    def get_today_cost(self, obj):
        """Get today's cost for the organization."""
        return float(obj.get_today_cost())
    
    def get_week_cost(self, obj):
        """Get this week's cost for the organization."""
        return float(obj.get_week_cost())
    
    def get_month_cost(self, obj):
        """Get this month's cost for the organization."""
        return float(obj.get_month_cost())


class ClientSerializer(serializers.ModelSerializer):
    """Serializer for Client model."""
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    projects_count = serializers.SerializerMethodField()
    organization_id = serializers.CharField(source='organization.id', read_only=True)
    
    class Meta:
        model = Client
        fields = [
            'id', 'organization', 'organization_id', 'organization_name', 'name', 'email',
            'cc_recipients', 'address', 'note', 'currency', 'projects_count',
            # Legacy fields for backward compatibility
            'contact_person', 'phone', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'projects_count', 'organization_id']
    
    def validate_cc_recipients(self, value):
        """Validate that cc_recipients has maximum 3 email addresses."""
        if value:
            emails = [email.strip() for email in value.split(',') if email.strip()]
            if len(emails) > 3:
                raise serializers.ValidationError("Maximum 3 email addresses allowed in Cc recipients.")
            # Validate email format
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError
            for email in emails:
                try:
                    validate_email(email)
                except ValidationError:
                    raise serializers.ValidationError(f"Invalid email address: {email}")
        return value
    
    def get_projects_count(self, obj):
        return obj.projects.filter(status='active').count()


class TeamSerializer(serializers.ModelSerializer):
    """Serializer for Team model."""
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    members_count = serializers.SerializerMethodField()
    organization = serializers.PrimaryKeyRelatedField(read_only=True)  # Make organization read-only - set in perform_create
    
    class Meta:
        model = Team
        fields = [
            'id', 'organization', 'organization_name', 'name', 'description',
            'is_active', 'members_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'organization', 'created_at', 'updated_at', 'members_count']
    
    def get_members_count(self, obj):
        return obj.members.filter(is_active=True).count()


class ProjectSerializer(serializers.ModelSerializer):
    """Serializer for Project model."""
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    client_name = serializers.CharField(source='client.name', read_only=True)
    tasks_count = serializers.SerializerMethodField()
    total_hours = serializers.SerializerMethodField()
    billable_hours = serializers.SerializerMethodField()
    
    class Meta:
        model = Project
        fields = [
            'id', 'organization', 'organization_name', 'client', 'client_name',
            'name', 'description', 'budget', 'hourly_rate', 'start_date', 'end_date',
            'status', 'is_billable', 'tasks_count', 'total_hours', 'billable_hours',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'tasks_count', 'total_hours', 'billable_hours']
    
    def get_tasks_count(self, obj):
        return obj.tasks.count()
    
    def get_total_hours(self, obj):
        entries = obj.time_entries.filter(end_time__isnull=False)
        return sum(float(entry.duration or 0) for entry in entries)
    
    def get_billable_hours(self, obj):
        entries = obj.time_entries.filter(end_time__isnull=False, is_billable=True)
        return sum(float(entry.duration or 0) for entry in entries)


class TaskSerializer(serializers.ModelSerializer):
    """Serializer for Task model."""
    project_name = serializers.CharField(source='project.name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.user.get_full_name', read_only=True)
    time_entries_count = serializers.SerializerMethodField()
    total_hours = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = [
            'id', 'project', 'project_name', 'name', 'description', 'assigned_to',
            'assigned_to_name', 'start_date', 'end_date', 'estimated_hours',
            'status', 'priority', 'time_entries_count', 'total_hours',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'time_entries_count', 'total_hours']
    
    def get_time_entries_count(self, obj):
        return obj.time_entries.count()
    
    def get_total_hours(self, obj):
        entries = obj.time_entries.filter(end_time__isnull=False)
        return sum(float(entry.duration or 0) for entry in entries)


class TimeEntrySerializer(serializers.ModelSerializer):
    """Serializer for TimeEntry model."""
    user_name = serializers.CharField(source='user.user.get_full_name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    task_name = serializers.CharField(source='task.name', read_only=True)
    duration_formatted = serializers.SerializerMethodField()
    
    class Meta:
        model = TimeEntry
        fields = [
            'id', 'user', 'user_name', 'project', 'project_name', 'task', 'task_name',
            'description', 'start_time', 'end_time', 'duration', 'duration_formatted',
            'is_billable', 'is_overtime', 'is_manual', 'tags', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'duration_formatted']
    
    def get_duration_formatted(self, obj):
        if obj.duration:
            hours = int(obj.duration)
            minutes = int((obj.duration - hours) * 60)
            return f"{hours:02d}:{minutes:02d}"
        return "00:00"
    
    def validate(self, data):
        """Validate time entry data."""
        if data.get('end_time') and data.get('start_time'):
            if data['end_time'] <= data['start_time']:
                raise serializers.ValidationError("End time must be after start time.")
        
        # Check for overlapping time entries
        if not data.get('is_manual', False):
            user = data['user']
            start_time = data['start_time']
            end_time = data.get('end_time')
            
            if end_time:
                overlapping = TimeEntry.objects.filter(
                    user=user,
                    start_time__lt=end_time,
                    end_time__gt=start_time,
                    is_active=True
                ).exclude(id=self.instance.id if self.instance else None)
                
                if overlapping.exists():
                    raise serializers.ValidationError("Time entry overlaps with existing entry.")
        
        return data


class LeaveRequestSerializer(serializers.ModelSerializer):
    """Serializer for LeaveRequest model."""
    user_name = serializers.CharField(source='user.user.get_full_name', read_only=True)
    user_email = serializers.CharField(source='user.user.email', read_only=True)
    user_id = serializers.CharField(source='user.user.id', read_only=True)
    team_name = serializers.CharField(source='user.team.name', read_only=True, allow_null=True)
    team_id = serializers.CharField(source='user.team.id', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.user.get_full_name', read_only=True, allow_null=True)
    duration_days = serializers.IntegerField(read_only=True)
    requested_hours_display = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(read_only=True)  # Make user read-only - set in perform_create
    start_time = serializers.TimeField(required=False, allow_null=True, input_formats=['%H:%M', '%H:%M:%S'])
    end_time = serializers.TimeField(required=False, allow_null=True, input_formats=['%H:%M', '%H:%M:%S'])
    
    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'user', 'user_id', 'user_name', 'user_email', 'team_name', 'team_id',
            'leave_type', 'start_date', 'end_date', 'start_time', 'end_time',
            'requested_hours', 'requested_hours_display', 'policy', 'reason', 'status',
            'approved_by', 'approved_by_name', 'approved_at',
            'rejection_reason', 'duration_days', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at', 'duration_days', 'requested_hours_display']
    
    def get_requested_hours_display(self, obj):
        """Format requested hours for display."""
        if obj.requested_hours:
            return f"{float(obj.requested_hours):.2f}h"
        return f"{obj.calculate_requested_hours():.2f}h"
    
    def validate(self, data):
        """Validate leave request data."""
        if data.get('end_date') and data.get('start_date'):
            if data['end_date'] < data['start_date']:
                raise serializers.ValidationError("End date must be after start date.")
        
        # Convert empty string times to None
        if 'start_time' in data and data['start_time'] == '':
            data['start_time'] = None
        if 'end_time' in data and data['end_time'] == '':
            data['end_time'] = None
        
        # Check for overlapping leave requests (only if we have user and instance)
        user = data.get('user') or (self.instance.user if self.instance else None)
        if user and data.get('start_date') and data.get('end_date'):
            start_date = data['start_date']
            end_date = data['end_date']
            
            overlapping = LeaveRequest.objects.filter(
                user=user,
                start_date__lte=end_date,
                end_date__gte=start_date,
                status__in=['pending', 'approved']
            ).exclude(id=self.instance.id if self.instance else None)
            
            if overlapping.exists():
                raise serializers.ValidationError("Leave request overlaps with existing request.")
        
        return data


class ExpenseSerializer(serializers.ModelSerializer):
    """Serializer for Expense model."""
    project_name = serializers.CharField(source='project.name', read_only=True)
    user_name = serializers.CharField(source='user.user.get_full_name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.user.get_full_name', read_only=True)
    
    class Meta:
        model = Expense
        fields = [
            'id', 'project', 'project_name', 'user', 'user_name', 'description',
            'amount', 'currency', 'category', 'date', 'receipt_url', 'status',
            'approved_by', 'approved_by_name', 'approved_at', 'rejection_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ActivityLogSerializer(serializers.ModelSerializer):
    """Serializer for ActivityLog model."""
    user_name = serializers.CharField(source='user.user.get_full_name', read_only=True)
    
    class Meta:
        model = ActivityLog
        fields = [
            'id', 'user', 'user_name', 'action', 'description', 'metadata',
            'ip_address', 'user_agent', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class KioskSerializer(serializers.ModelSerializer):
    """Serializer for Kiosk model."""
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    active_sessions_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Kiosk
        fields = [
            'id', 'organization', 'organization_name', 'name', 'location',
            'is_active', 'active_sessions_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'active_sessions_count']
    
    def get_active_sessions_count(self, obj):
        return obj.sessions.filter(is_active=True).count()


class KioskSessionSerializer(serializers.ModelSerializer):
    """Serializer for KioskSession model."""
    kiosk_name = serializers.CharField(source='kiosk.name', read_only=True)
    user_name = serializers.CharField(source='user.user.get_full_name', read_only=True)
    duration_hours = serializers.SerializerMethodField()
    
    class Meta:
        model = KioskSession
        fields = [
            'id', 'kiosk', 'kiosk_name', 'user', 'user_name', 'check_in_time',
            'check_out_time', 'is_active', 'duration_hours', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'duration_hours']
    
    def get_duration_hours(self, obj):
        duration = obj.duration
        return round(duration, 2) if duration else None


# Authentication Serializers
class LoginSerializer(serializers.Serializer):
    """Serializer for user login."""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    
    def validate(self, data):
        email = data.get('email')
        password = data.get('password')
        
        if email and password:
            user = authenticate(username=email, password=password)
            if not user:
                raise serializers.ValidationError('Invalid credentials.')
            if not user.is_active:
                raise serializers.ValidationError('User account is disabled.')
            data['user'] = user
        else:
            raise serializers.ValidationError('Must include email and password.')
        
        return data


class RegisterSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'password', 'password_confirm']
    
    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError("Passwords don't match.")
        return data
    
    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for password change."""
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(required=True)
    
    def validate(self, data):
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError("New passwords don't match.")
        return data
    
    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value
