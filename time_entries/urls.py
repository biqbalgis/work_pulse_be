from rest_framework.routers import DefaultRouter
from .views import TimeEntryViewSet, BulkTimeEntryViewSet

router = DefaultRouter()
router.register(r'time-entries', TimeEntryViewSet, basename='time-entry')
router.register(r'time_entries/bulk-create', BulkTimeEntryViewSet, basename='bulk-create')
urlpatterns = router.urls
