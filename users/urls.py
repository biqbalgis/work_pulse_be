from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView
from .views import (
    CustomTokenObtainPairView, RegisterView, ChangePasswordView,
    logout_view, user_profile_view, update_user_profile_view
)

urlpatterns = [
    # Authentication URLs
    path('login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),
    path('logout/', logout_view, name='logout'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    
    # User profile URLs
    path('profile/', user_profile_view, name='user_profile'),
    path('profile/update/', update_user_profile_view, name='update_user_profile'),
]
