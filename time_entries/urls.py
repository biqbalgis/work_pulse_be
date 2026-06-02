from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import TimeEntryViewSet, BulkTimeEntryViewSet, BulkTimeEntryEditViewSet, WeeklyHoursSummaryView
from .field_ticket_views import FieldTicketBulkEntryView

router = DefaultRouter()
router.register(r'time-entries', TimeEntryViewSet, basename='time-entry')
router.register(r'time_entries/bulk-create', BulkTimeEntryViewSet, basename='bulk-create')
router.register(r'time_entries/bulk-edit', BulkTimeEntryEditViewSet, basename='bulk-edit')

urlpatterns = router.urls + [
    path("time_entries/weekly-hours-summary/", WeeklyHoursSummaryView.as_view(), name="weekly-hours-summary"),
    path("time_entries/field-ticket-entry/", FieldTicketBulkEntryView.as_view(), name="field-ticket-entry"),
]
