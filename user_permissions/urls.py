from django.urls import path
from .views import UserPermissionDetailView, MyPermissionsView

urlpatterns = [
    path("user-permissions/me/",        MyPermissionsView.as_view(),          name="my-permissions"),
    path("user-permissions/<uuid:user_id>/", UserPermissionDetailView.as_view(), name="user-permissions"),
]
