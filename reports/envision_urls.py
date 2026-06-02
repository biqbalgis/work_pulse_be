from django.urls import path
from .envision_views import EnvisionLEMReportView

urlpatterns = [
    path("fieldTicket_Lem/", EnvisionLEMReportView.as_view(), name="envision-field-ticket-lem"),
]
