from django.urls import path
from .envision_views import EnvisionLEMReportView, EnvisionLEMSearchView

urlpatterns = [
    path("fieldTicket_Lem/", EnvisionLEMReportView.as_view(), name="envision-field-ticket-lem"),
    path("lem/search/", EnvisionLEMSearchView.as_view(), name="envision-lem-search"),
]
