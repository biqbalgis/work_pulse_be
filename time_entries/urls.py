from rest_framework.routers import DefaultRouter
from .views import TimeEntryViewSet

router = DefaultRouter()
router.register(r'time-entries', TimeEntryViewSet, basename='time-entry')
urlpatterns = router.urls
