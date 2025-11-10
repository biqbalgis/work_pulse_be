from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from decimal import Decimal
import uuid


class User(AbstractUser):
    """Custom User model extending Django's AbstractUser."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=30)
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    is_email_verified = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    class Meta:
        ordering = ['first_name', 'last_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name


class UserProfile(models.Model):
    """Extended user profile for WorkPulse specific data."""
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('supervisor', 'Supervisor'),
        ('member', 'Member'),
        ('viewer', 'Viewer'),
    ]

    BILLING_TYPE_CHOICES = [
        ('hourly', 'Hourly'),
        ('monthly', 'Monthly'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, related_name='user_profiles')
    team = models.ForeignKey('core.Team', on_delete=models.SET_NULL, null=True, blank=True, related_name='members')
    supervisor = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subordinates')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member', help_text="Primary role (deprecated - use roles)")
    roles = models.JSONField(default=list, blank=True, help_text="List of user roles. Options: admin, supervisor, member, viewer")
    billing_rate = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    billing_type = models.CharField(max_length=10, choices=BILLING_TYPE_CHOICES, default='hourly')
    daily_capacity = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('8.00'), 
                                        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('24.00'))])
    working_days = models.JSONField(default=list, help_text="List of working days (0=Monday, 6=Sunday)")
    week_start = models.IntegerField(default=1, help_text="Week start day (0=Monday, 6=Sunday)")
    timezone = models.CharField(max_length=50, default='America/Toronto')
    is_active = models.BooleanField(default=True)
    hire_date = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.organization.name})"

    def get_roles(self):
        """Get list of roles, with backward compatibility for single role."""
        if self.roles and isinstance(self.roles, list) and len(self.roles) > 0:
            return self.roles
        # Backward compatibility: return single role as list
        return [self.role] if self.role else ['member']
    
    def has_role(self, role):
        """Check if user has a specific role."""
        return role in self.get_roles()
    
    def add_role(self, role):
        """Add a role to the user."""
        roles = self.get_roles()
        if role not in roles and role in [choice[0] for choice in self.ROLE_CHOICES]:
            roles.append(role)
            self.roles = roles
            self.save(update_fields=['roles'])
    
    def remove_role(self, role):
        """Remove a role from the user."""
        roles = self.get_roles()
        if role in roles and len(roles) > 1:  # Keep at least one role
            roles.remove(role)
            self.roles = roles
            self.save(update_fields=['roles'])

    @property
    def is_admin(self):
        return 'admin' in self.get_roles()

    @property
    def is_supervisor(self):
        return any(role in self.get_roles() for role in ['admin', 'supervisor'])

    @property
    def can_approve_timesheets(self):
        return any(role in self.get_roles() for role in ['admin', 'supervisor'])

    @property
    def can_manage_users(self):
        return any(role in self.get_roles() for role in ['admin', 'supervisor'])

    @property
    def can_view_reports(self):
        return any(role in self.get_roles() for role in ['admin', 'supervisor', 'member'])

    def get_subordinates(self):
        """Get all users supervised by this user."""
        return UserProfile.objects.filter(supervisor=self)

    def get_current_timer(self):
        """Get the current active timer for this user."""
        from core.models import TimeEntry
        return TimeEntry.objects.filter(user=self, is_active=True, end_time__isnull=True).first()

    def get_today_hours(self):
        """Get total hours worked today."""
        from core.models import TimeEntry
        today = timezone.now().date()
        entries = TimeEntry.objects.filter(
            user=self,
            start_time__date=today,
            end_time__isnull=False
        )
        return sum(entry.duration or 0 for entry in entries)

    def get_week_hours(self, week_start=None):
        """Get total hours worked this week."""
        from core.models import TimeEntry
        from datetime import timedelta
        
        if not week_start:
            today = timezone.now().date()
            days_since_week_start = (today.weekday() - self.week_start) % 7
            week_start = today - timedelta(days=days_since_week_start)
        
        week_end = week_start + timedelta(days=6)
        
        entries = TimeEntry.objects.filter(
            user=self,
            start_time__date__gte=week_start,
            start_time__date__lte=week_end,
            end_time__isnull=False
        )
        return sum(entry.duration or 0 for entry in entries)

    def get_month_hours(self, month=None, year=None):
        """Get total hours worked this month."""
        from core.models import TimeEntry
        from datetime import date
        
        if not month:
            month = timezone.now().month
        if not year:
            year = timezone.now().year
        
        entries = TimeEntry.objects.filter(
            user=self,
            start_time__year=year,
            start_time__month=month,
            end_time__isnull=False
        )
        return sum(entry.duration or 0 for entry in entries)

    def get_utilization_percentage(self, period='week'):
        """Calculate utilization percentage for a given period."""
        if period == 'week':
            actual_hours = self.get_week_hours()
            capacity_hours = self.daily_capacity * len(self.working_days)
        elif period == 'month':
            actual_hours = self.get_month_hours()
            # Approximate monthly capacity (4.33 weeks * weekly capacity)
            capacity_hours = self.daily_capacity * len(self.working_days) * 4.33
        else:
            return 0
        
        if capacity_hours == 0:
            return 0
        
        return min(100, (actual_hours / capacity_hours) * 100)