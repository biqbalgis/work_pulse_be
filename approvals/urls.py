from rest_framework.routers import DefaultRouter
from .views import TimeEntryApprovalViewSet

router = DefaultRouter()
router.register(r'approvals', TimeEntryApprovalViewSet, basename='approval')
urlpatterns = router.urls
