from django.contrib import admin
from .models import TimeEntryApproval, TimeEntryApprovalItem

@admin.register(TimeEntryApproval)
class TimeEntryApprovalAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeEntryApproval._meta.fields]

@admin.register(TimeEntryApprovalItem)
class TimeEntryApprovalItemAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeEntryApprovalItem._meta.fields]
