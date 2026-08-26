from django.contrib import admin
from .models import Project, JobTitle, ProjectRole, UserProjectRole


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [f.name for f in Project._meta.fields]


@admin.register(JobTitle)
class JobTitleAdmin(admin.ModelAdmin):
    list_display = ("id", "name")


@admin.register(ProjectRole)
class ProjectRoleAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "job_title", "hourly_rate")


@admin.register(UserProjectRole)
class UserProjectRoleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "project", "job_title", "hourly_rate")