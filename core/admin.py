from django.contrib import admin
from core.models import ActivityLog, ErrorLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'action', 'model_name', 'ip_address']
    list_filter = ['action', 'model_name']
    search_fields = ['user__username', 'action', 'model_name']

@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'message', 'path', 'ip_address']
    list_filter = ['method']
    search_fields = ['message', 'traceback', 'path', 'user__username']