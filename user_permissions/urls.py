from django.urls import path

from .views import (
    CatalogView, MemberAccessListView, MyPermissionsView, PermissionGroupDetailView, PermissionGroupListView,
    UserPermissionDetailView,
)

urlpatterns = [
    path("user-permissions/catalog/", CatalogView.as_view(), name="permission-catalog"),
    path("user-permissions/me/", MyPermissionsView.as_view(), name="my-permissions"),
    path("user-permissions/", MemberAccessListView.as_view(), name="member-access-list"),
    path("user-permissions/<uuid:user_id>/", UserPermissionDetailView.as_view(), name="user-permissions"),
    path("permission-groups/", PermissionGroupListView.as_view(), name="permission-groups"),
    path("permission-groups/<uuid:pk>/", PermissionGroupDetailView.as_view(), name="permission-group"),
]
