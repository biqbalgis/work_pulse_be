from django.contrib import admin
from .models import TimeOffType, TimeOffRequest

@admin.register(TimeOffType)
class TimeOffTypeAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeOffType._meta.fields]

@admin.register(TimeOffRequest)
class TimeOffRequestAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeOffRequest._meta.fields]
