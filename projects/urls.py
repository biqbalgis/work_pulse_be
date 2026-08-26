from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, JobTitleViewSet, ProjectRoleViewSet, UserProjectRoleViewSet, CurrentUserProjectRolesView, RoleTemplateViewSet
from django.urls import path

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='projects')
router.register('job-titles', JobTitleViewSet, basename='job-titles')
router.register('project-roles', ProjectRoleViewSet, basename='project-roles')
router.register('user-project-roles', UserProjectRoleViewSet, basename='user-project-roles')
router.register('role-templates', RoleTemplateViewSet, basename='role-templates')


urlpatterns = router.urls + [
    path('current-user-roles/', CurrentUserProjectRolesView.as_view(), name='current-user-roles'),
]
