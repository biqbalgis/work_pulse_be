from django.contrib import admin

from .models import DailyTicketDownload


@admin.register(DailyTicketDownload)
class DailyTicketDownloadAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "downloaded_at", "downloaded_by", "workspace", "ip_address")
    list_filter = ("workspace",)
    search_fields = ("ticket_number", "downloaded_by__email", "downloaded_by__first_name", "downloaded_by__last_name")
    readonly_fields = ("sequence", "ticket_number", "downloaded_at", "downloaded_by", "workspace", "ip_address")

    # The log is an audit trail: numbers are only ever issued by downloading a ticket.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
