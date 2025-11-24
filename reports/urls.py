from django.urls import path
from .views import WeeklyPayrollReport, DailyRTOTReport, DailyDetailView, EmployeePayrollDashboard

urlpatterns = [
    path("weekly-payroll/", WeeklyPayrollReport.as_view(), name="weekly-payroll"),
    path("daily-hours/", DailyRTOTReport.as_view(), name="report-daily-hours"),
    path("daily-hours/<uuid:employee_id>/<str:date>/",DailyDetailView.as_view(),name="daily-hours-detail"),

    path("payroll/employees/", EmployeePayrollDashboard.as_view()),
]