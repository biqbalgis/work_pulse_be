from django.contrib import admin
from .models import Tag, TimeEntryTag

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = [f.name for f in Tag._meta.fields]

@admin.register(TimeEntryTag)
class TimeEntryTagAdmin(admin.ModelAdmin):
    list_display = [f.name for f in TimeEntryTag._meta.fields]
