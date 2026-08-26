from django.urls import path
from .views import WeeklyPayrollReport, DailyRTOTReport, DailyDetailView, EmployeePayrollDashboard, DailyWorkReportView, \
    LEMReportGenerationView, LEMDailyReportView, LEMCostingReportView, TimeEntryExcelReportView
from .dashboard_views import (
    DashboardSummaryView,
    DashboardHoursByProjectView,
    DashboardFieldTicketsView,
    DashboardAssetCostsView,
    DashboardMyHoursByProjectView,
    DashboardMyHoursByTaskView,
)

urlpatterns = [
    path("weekly-payroll/", WeeklyPayrollReport.as_view(), name="weekly-payroll"),
    path("daily-hours/", DailyRTOTReport.as_view(), name="report-daily-hours"),
    path("daily-hours/<uuid:employee_id>/<str:date>/",DailyDetailView.as_view(),name="daily-hours-detail"),
    path("lem_report_generation/", DailyWorkReportView.as_view(), name="daily-work-report"),
    path("lem-report/generate/", LEMReportGenerationView.as_view(), name="lem-report-generate"),
    path("lem-report/dailylemreport/", LEMDailyReportView.as_view(), name="lem-report-generate"),
    path("lem-report/costing/", LEMCostingReportView.as_view(), name="lem-report-costing"),
    path("payroll/employees/", EmployeePayrollDashboard.as_view()),
    path("time-entry-excel/", TimeEntryExcelReportView.as_view(), name="time-entry-excel-report"),
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("dashboard/hours-by-project/", DashboardHoursByProjectView.as_view(), name="dashboard-hours-by-project"),
    path("dashboard/field-tickets/", DashboardFieldTicketsView.as_view(), name="dashboard-field-tickets"),
    path("dashboard/asset-costs/", DashboardAssetCostsView.as_view(), name="dashboard-asset-costs"),
    path("dashboard/my-hours-by-project/", DashboardMyHoursByProjectView.as_view(), name="dashboard-my-hours-by-project"),
    path("dashboard/my-hours-by-task/", DashboardMyHoursByTaskView.as_view(), name="dashboard-my-hours-by-task"),
]