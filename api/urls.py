from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.views import (
    OrganizationViewSet, ClientViewSet, TeamViewSet, ProjectViewSet,
    TaskViewSet, TimeEntryViewSet, LeaveRequestViewSet, ExpenseViewSet,
    ActivityLogViewSet, KioskViewSet, KioskSessionViewSet
)
from reports.views import ReportsViewSet
from users.views import UserViewSet

# Create router and register viewsets
router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'organizations', OrganizationViewSet)
router.register(r'clients', ClientViewSet)
router.register(r'teams', TeamViewSet)
router.register(r'projects', ProjectViewSet)
router.register(r'tasks', TaskViewSet)
router.register(r'time-entries', TimeEntryViewSet)
router.register(r'leave-requests', LeaveRequestViewSet)
router.register(r'expenses', ExpenseViewSet)
router.register(r'activity', ActivityLogViewSet)
router.register(r'kiosks', KioskViewSet)
router.register(r'kiosk-sessions', KioskSessionViewSet)
router.register(r'reports', ReportsViewSet, basename='reports')

urlpatterns = [
    path('', include(router.urls)),
]
