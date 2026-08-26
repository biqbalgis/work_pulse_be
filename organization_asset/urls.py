from rest_framework.routers import DefaultRouter
from .views import OrganizationAssetViewSet

router = DefaultRouter()
router.register("", OrganizationAssetViewSet, basename="organization-assets")

urlpatterns = router.urls
