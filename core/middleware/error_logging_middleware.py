import traceback
from django.utils.deprecation import MiddlewareMixin
from core.models import ErrorLog
from core.utils.logger import get_client_ip

class ErrorLoggingMiddleware(MiddlewareMixin):
    """
    Logs all unhandled exceptions (with traceback, user, IP, etc.) into ErrorLog table.
    """

    def process_exception(self, request, exception):
        tb = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))

        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
        user = request.user if request.user.is_authenticated else None

        ErrorLog.objects.create(
            user=user,
            message=str(exception),
            traceback=tb,
            path=request.path,
            method=request.method,
            ip_address=ip,
            user_agent=ua,
            extra_data={"params": dict(request.GET), "body": request.body.decode('utf-8', errors='ignore')},
        )

        # Let Django handle the response (still shows DRF error to client)
        return None
