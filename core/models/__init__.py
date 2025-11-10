from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.apps import apps
from decimal import Decimal
import uuid


class TimeStampedModel(models.Model):
    """Abstract base class with self-updating created and modified fields."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimeStampedModel):
    """Organization model for multi-tenant support."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    timezone = models.CharField(max_length=50, default='America/Toronto')
    working_days = models.JSONField(default=list, help_text="List of working days (0=Monday, 6=Sunday)")
    public_holidays = models.JSONField(default=list, help_text="List of public holidays")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
    
    def get_today_cost(self):
        """Calculate total cost for today for all users in this organization."""
        from datetime import timedelta
        
        today = timezone.now().date()
        total_cost = Decimal('0.00')
        
        # Get all active users in this organization
        users = self.user_profiles.filter(is_active=True)
        
        for user_profile in users:
            # Get all time entries for today for this user
            # TimeEntry is defined later in this file
            TimeEntry = apps.get_model('core', 'TimeEntry')
            time_entries = TimeEntry.objects.filter(
                user=user_profile,
                start_time__date=today,
                end_time__isnull=False,
                is_billable=True
            )
            
            # Calculate cost for each time entry
            for entry in time_entries:
                total_cost += entry.get_billing_amount()
        
        return total_cost
    
    def get_week_cost(self, week_start=None):
        """Calculate total cost for the current week for all users in this organization."""
        from datetime import timedelta
        
        today = timezone.now().date()
        
        # Calculate week start (Monday = 0, Sunday = 6)
        days_since_monday = today.weekday()
        week_start_date = today - timedelta(days=days_since_monday) if week_start is None else week_start
        week_end_date = week_start_date + timedelta(days=6)
        
        total_cost = Decimal('0.00')
        
        # Get all active users in this organization
        users = self.user_profiles.filter(is_active=True)
        TimeEntry = apps.get_model('core', 'TimeEntry')
        
        for user_profile in users:
            # Get all time entries for this week for this user
            time_entries = TimeEntry.objects.filter(
                user=user_profile,
                start_time__date__gte=week_start_date,
                start_time__date__lte=week_end_date,
                end_time__isnull=False,
                is_billable=True
            )
            
            # Calculate cost for each time entry
            for entry in time_entries:
                total_cost += entry.get_billing_amount()
        
        return total_cost
    
    def get_month_cost(self, month=None, year=None):
        """Calculate total cost for the current month for all users in this organization."""
        today = timezone.now().date()
        if not month:
            month = today.month
        if not year:
            year = today.year
        
        total_cost = Decimal('0.00')
        
        # Get all active users in this organization
        users = self.user_profiles.filter(is_active=True)
        TimeEntry = apps.get_model('core', 'TimeEntry')
        
        for user_profile in users:
            # Get all time entries for this month for this user
            time_entries = TimeEntry.objects.filter(
                user=user_profile,
                start_time__year=year,
                start_time__month=month,
                end_time__isnull=False,
                is_billable=True
            )
            
            # Calculate cost for each time entry
            for entry in time_entries:
                total_cost += entry.get_billing_amount()
        
        return total_cost


class Client(TimeStampedModel):
    """Client model for organizations."""
    CURRENCY_CHOICES = [
        ('CAD', 'CAD'),
        ('USD', 'USD'),
        ('EUR', 'EUR'),
        ('GBP', 'GBP'),
        ('JPY', 'JPY'),
        ('AUD', 'AUD'),
        ('CHF', 'CHF'),
        ('CNY', 'CNY'),
        ('INR', 'INR'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='clients')
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    cc_recipients = models.TextField(blank=True, null=True, help_text="Comma-separated email addresses (max 3)")
    address = models.TextField(blank=True, null=True)
    note = models.TextField(blank=True, null=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='CAD')
    # Legacy fields kept for backward compatibility
    contact_person = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ['organization', 'name']

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class Team(TimeStampedModel):
    """Team model for grouping users."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='teams')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ['organization', 'name']

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class Project(TimeStampedModel):
    """Project model for tracking work."""
    PROJECT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('completed', 'Completed'),
        ('on_hold', 'On Hold'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='projects')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=PROJECT_STATUS_CHOICES, default='active')
    is_billable = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ['organization', 'name']

    def __str__(self):
        return f"{self.name} ({self.client.name})"


class Task(TimeStampedModel):
    """Task model for project breakdown."""
    TASK_STATUS_CHOICES = [
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('review', 'Review'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    assigned_to = models.ForeignKey('users.UserProfile', on_delete=models.SET_NULL, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=TASK_STATUS_CHOICES, default='todo')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.project.name})"


class TimeEntry(TimeStampedModel):
    """Time entry model for tracking work time."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='time_entries')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='time_entries')
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='time_entries')
    description = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="Duration in hours")
    is_billable = models.BooleanField(default=True)
    is_overtime = models.BooleanField(default=False, help_text="Overtime hours charged at double rate")
    is_manual = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, help_text="Whether this is an active timer")

    class Meta:
        ordering = ['-start_time']
        verbose_name_plural = 'Time Entries'

    def __str__(self):
        return f"{self.user.user.get_full_name()} - {self.project.name} ({self.start_time.date()})"

    def save(self, *args, **kwargs):
        # Calculate duration if end_time is provided
        if self.end_time and not self.duration:
            delta = self.end_time - self.start_time
            self.duration = Decimal(str(delta.total_seconds() / 3600)).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    @property
    def is_running(self):
        """Check if this is an active timer."""
        return self.is_active and self.end_time is None
    
    def get_billing_rate(self):
        """Get billing rate, double if overtime."""
        if not self.is_billable:
            return Decimal('0.00')
        # Get user's billing rate
        user_rate = self.user.billing_rate or Decimal('0.00')
        # Double if overtime
        return user_rate * Decimal('2.0') if self.is_overtime else user_rate
    
    def get_billing_amount(self):
        """Calculate billing amount (hours * rate * 2 if overtime)."""
        if not self.is_billable or not self.duration:
            return Decimal('0.00')
        return self.duration * self.get_billing_rate()


class LeaveRequest(TimeStampedModel):
    """Leave request model for time off management."""
    LEAVE_TYPE_CHOICES = [
        ('vacation', 'Vacation'),
        ('sick_leave', 'Sick Leave'),
        ('public_holiday', 'Public Holiday'),
        ('personal', 'Personal'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True, help_text="Start time for partial day requests")
    end_time = models.TimeField(null=True, blank=True, help_text="End time for partial day requests")
    requested_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="Total hours requested")
    policy = models.CharField(max_length=100, blank=True, null=True, help_text="Policy type (e.g., Personal Time Off (3 Weeks) - Employees)")
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey('users.UserProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_leave_requests')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.user.get_full_name()} - {self.leave_type} ({self.start_date} to {self.end_date})"

    @property
    def duration_days(self):
        """Calculate the duration in days."""
        return (self.end_date - self.start_date).days + 1
    
    def calculate_requested_hours(self):
        """Calculate requested hours based on dates and times."""
        if self.requested_hours:
            return float(self.requested_hours)
        
        # Calculate based on date range
        days = self.duration_days
        
        # If start_time and end_time are provided for same-day requests
        if self.start_date == self.end_date and self.start_time and self.end_time:
            from datetime import datetime
            start = datetime.combine(self.start_date, self.start_time)
            end = datetime.combine(self.end_date, self.end_time)
            delta = end - start
            return delta.total_seconds() / 3600
        
        # Default: assume 8 hours per day
        return days * 8
    
    def save(self, *args, **kwargs):
        """Auto-calculate requested_hours if not provided."""
        if not self.requested_hours:
            self.requested_hours = self.calculate_requested_hours()
        super().save(*args, **kwargs)


class Expense(TimeStampedModel):
    """Expense model for project-related costs."""
    EXPENSE_CATEGORIES = [
        ('travel', 'Travel'),
        ('meals', 'Meals'),
        ('supplies', 'Supplies'),
        ('software', 'Software'),
        ('hardware', 'Hardware'),
        ('training', 'Training'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='expenses')
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='expenses')
    description = models.TextField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    currency = models.CharField(max_length=3, default='CAD')
    category = models.CharField(max_length=20, choices=EXPENSE_CATEGORIES)
    date = models.DateField()
    receipt_url = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey('users.UserProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_expenses')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.description} - ${self.amount} ({self.project.name})"


class ActivityLog(TimeStampedModel):
    """Activity log for tracking user actions."""
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('timer_start', 'Timer Started'),
        ('timer_stop', 'Timer Stopped'),
        ('time_entry_created', 'Time Entry Created'),
        ('time_entry_updated', 'Time Entry Updated'),
        ('time_entry_deleted', 'Time Entry Deleted'),
        ('project_created', 'Project Created'),
        ('project_updated', 'Project Updated'),
        ('leave_requested', 'Leave Requested'),
        ('leave_approved', 'Leave Approved'),
        ('leave_rejected', 'Leave Rejected'),
        ('expense_submitted', 'Expense Submitted'),
        ('expense_approved', 'Expense Approved'),
        ('expense_rejected', 'Expense Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.user.get_full_name()} - {self.action}"


class Kiosk(TimeStampedModel):
    """Kiosk model for check-in/check-out terminals."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='kiosks')
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.location})"


class KioskSession(TimeStampedModel):
    """Kiosk session for tracking check-in/check-out."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kiosk = models.ForeignKey(Kiosk, on_delete=models.CASCADE, related_name='sessions')
    user = models.ForeignKey('users.UserProfile', on_delete=models.CASCADE, related_name='kiosk_sessions')
    check_in_time = models.DateTimeField()
    check_out_time = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-check_in_time']

    def __str__(self):
        return f"{self.user.user.get_full_name()} - {self.kiosk.name} ({self.check_in_time.date()})"

    @property
    def duration(self):
        """Calculate session duration."""
        if self.check_out_time:
            delta = self.check_out_time - self.check_in_time
            return delta.total_seconds() / 3600  # Return hours
        return None
