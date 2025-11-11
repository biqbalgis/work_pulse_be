import json
from django.utils.deprecation import MiddlewareMixin
from core.models import ActivityLog
from core.utils.logger import get_client_ip

class ActivityLoggingMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith("/admin") or request.path.startswith("/static"):
            return None

        if request.path.startswith("/api"):
            user = request.user if request.user.is_authenticated else None
            ip = get_client_ip(request)
            ua = request.META.get('HTTP_USER_AGENT', '')

            body = None
            try:
                body = json.loads(request.body.decode("utf-8")) if request.body else None
            except Exception:
                body = str(request.body)

            ActivityLog.objects.create(
                user=user,
                action=request.method,
                model_name=request.resolver_match.view_name,
                extra_data={
                    "path": request.path,
                    "params": dict(request.GET),
                    "body": body,
                },
                ip_address=ip,
                user_agent=ua,
            )
        return None
