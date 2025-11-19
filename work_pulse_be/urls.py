from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),

    # 🔐 JWT Authentication
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # 🧩 Core App
    path('api/core/', include('core.urls')),

    # 👥 Users
    path('api/', include('users.urls')),

    # 🏢 Workspaces
    path('api/', include('workspaces.urls')),

    # 👨‍💼 Clients
    path('api/', include('clients.urls')),

    # 📁 Projects
    path('api/', include('projects.urls')),

    # ✅ Tasks
    path('api/', include('tasks.urls')),

    # 🕒 Time Entries
    path('api/', include('time_entries.urls')),

    # 🧾 Approvals
    path('api/', include('approvals.urls')),

    # 🌴 Time Off
    path('api/', include('time_off.urls')),

    # 🏷️ Tags
    path('api/', include('tags.urls')),

    #  Reports
    path("api/reports/", include("reports.urls")),
]