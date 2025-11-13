from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, JobTitleViewSet, ProjectRoleViewSet, UserProjectRoleViewSet

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='projects')
router.register('job-titles', JobTitleViewSet, basename='job-titles')
router.register('project-roles', ProjectRoleViewSet, basename='project-roles')
router.register('user-project-roles', UserProjectRoleViewSet, basename='user-project-roles')


urlpatterns = router.urls
