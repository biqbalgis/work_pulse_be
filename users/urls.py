from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ChangePasswordView,
    EmailLoginView,
    ForgotPasswordView,
    MeView,
    RegisterView,
    ResetPasswordConfirmView,
    UserViewSet,
)
from django.urls import path, include
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    # 🔹 JWT Authentication endpoints
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', EmailLoginView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', MeView.as_view(), name='me'),
    # 🔹 Password reset / change
    path('auth/forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('auth/reset-password/', ResetPasswordConfirmView.as_view(), name='reset_password_confirm'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    # 🔹 Include all viewset routes
    path('', include(router.urls)),
]