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
                if request.body:
                    body_data = json.loads(request.body.decode("utf-8"))
                    body = self.redact_sensitive_data(body_data)
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

    def redact_sensitive_data(self, data):
        """
        Recursively redact sensitive keys in a dictionary or list.
        """
        SENSITIVE_KEYS = {'password', 'refresh', 'access', 'token', 'secret', 'confirm_password'}
        
        if isinstance(data, dict):
            new_data = data.copy()
            for key, value in new_data.items():
                if key.lower() in SENSITIVE_KEYS:
                    new_data[key] = "[REDACTED]"
                else:
                    new_data[key] = self.redact_sensitive_data(value)
            return new_data
        elif isinstance(data, list):
            return [self.redact_sensitive_data(item) for item in data]
        else:
            return data
