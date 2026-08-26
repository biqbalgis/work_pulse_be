from django.urls import path
from .envision_views import (
    EnvisionLEMReportView,
    EnvisionFieldTicketLEMFromPayloadView,
    EnvisionLEMSearchView,
    EnvisionLEMVoidView,
    EnvisionCostingLEMView,
    EnvisionCostingLEMExcelView,
)
from .envision_timesheet_views import EnvisionTimesheetReportView

urlpatterns = [
    path("fieldTicket_Lem/",         EnvisionLEMReportView.as_view(),  name="envision-field-ticket-lem"),
    path("fieldTicket_Lem/payload/", EnvisionFieldTicketLEMFromPayloadView.as_view(), name="envision-field-ticket-lem-payload"),
    path("costing-lem/",             EnvisionCostingLEMView.as_view(), name="envision-costing-lem"),
    path("costing-lem/excel/",       EnvisionCostingLEMExcelView.as_view(), name="envision-costing-lem-excel"),
    path("lem/search/",              EnvisionLEMSearchView.as_view(),  name="envision-lem-search"),
    path("lem/void/",                EnvisionLEMVoidView.as_view(),    name="envision-lem-void"),
    path("payroll/",                 EnvisionTimesheetReportView.as_view(), name="envision-payroll"),
]
