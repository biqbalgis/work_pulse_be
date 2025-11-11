from django.contrib import admin
from .models import TimeEntry

@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeEntry._meta.fields]
