from rest_framework.routers import DefaultRouter
from .views import CrewViewSet, WorkspaceViewSet, WorkspaceMemberViewSet

router = DefaultRouter()
router.register(r'workspace', WorkspaceViewSet, basename='workspace')
router.register(r'workspace-members', WorkspaceMemberViewSet, basename='workspace-member')
router.register(r'crews', CrewViewSet, basename='crew')

urlpatterns = router.urls
