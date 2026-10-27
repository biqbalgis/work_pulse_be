from django.urls import path

from .views import PayRulesView

app_name = 'core'
urlpatterns = [
    path("pay-rules/", PayRulesView.as_view(), name="pay-rules"),
]
