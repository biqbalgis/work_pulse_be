from rest_framework.routers import DefaultRouter
from .views import TimeEntryApprovalViewSet, TimeEntryApprovalItemViewSet

router = DefaultRouter()
router.register("weeks", TimeEntryApprovalViewSet, basename="approvals")
router.register("items", TimeEntryApprovalItemViewSet, basename="approval-items")
urlpatterns = router.urls
