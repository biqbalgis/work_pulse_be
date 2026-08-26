from django.contrib import admin
from .models import User

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'username', 'email', 'is_active', 'is_deleted', 'is_superuser', 'date_joined']
    list_filter = ['is_active', 'is_deleted', 'is_superuser']
    actions = ['reactivate_users']

    def get_queryset(self, request):
        # The default manager hides soft-deleted users; show them here too,
        # since finding and reactivating a deleted user is the whole point
        # of this screen for a superuser.
        return self.model.all_objects.all()

    @admin.action(description="Reactivate selected users")
    def reactivate_users(self, request, queryset):
        queryset.update(is_active=True, is_deleted=False, deleted_at=None)
