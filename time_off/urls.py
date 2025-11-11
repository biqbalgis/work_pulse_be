from rest_framework.routers import DefaultRouter
from .views import TimeOffTypeViewSet, TimeOffRequestViewSet

router = DefaultRouter()
router.register(r'timeoff/types', TimeOffTypeViewSet, basename='timeoff-type')
router.register(r'timeoff/requests', TimeOffRequestViewSet, basename='timeoff-request')
urlpatterns = router.urls
