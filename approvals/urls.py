from rest_framework.routers import DefaultRouter
from .views import TimeEntryApprovalViewSet

router = DefaultRouter()
router.register("approvals", TimeEntryApprovalViewSet, basename="approvals")

urlpatterns = router.urls
