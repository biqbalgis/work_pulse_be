from django.utils.deprecation import MiddlewareMixin
from core.models import ActivityLog
from core.utils.logger import get_client_ip
from core.utils.redaction import redact_sensitive_data, safe_body_for_log


class ActivityLoggingMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith("/admin") or request.path.startswith("/static"):
            return None

        if request.path.startswith("/api"):
            user = request.user if request.user.is_authenticated else None
            ip = get_client_ip(request)
            ua = request.META.get('HTTP_USER_AGENT', '')

            # Redacted JSON / form fields; for anything else (uploads, unparseable or huge bodies) only the
            # content type and size are stored, never the raw bytes, which may contain passwords.
            body = safe_body_for_log(request)

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
        """Kept for compatibility; the logic lives in core.utils.redaction."""
        return redact_sensitive_data(data)
