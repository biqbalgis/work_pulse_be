from django.contrib import admin
from core.models import ActivityLog

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = [f.name for f in ActivityLog._meta.fields]
