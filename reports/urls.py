from django.urls import path
from .views import WeeklyPayrollReport

urlpatterns = [
    path("weekly-payroll/", WeeklyPayrollReport.as_view(), name="weekly-payroll"),
]