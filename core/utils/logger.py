from core.models import ActivityLog
from django.utils.timezone import now

def get_client_ip(request):
    """Extract real client IP from request headers (handles reverse proxies)."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def log_activity(user, action, model_name, object_id=None, extra_data=None, request=None):
    """Log any user activity with optional request context (IP, UA, location)."""
    ip = None
    ua = None

    if extra_data is None:
        extra_data = {}

    if request is not None:
        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')

    ActivityLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        action=action,
        model_name=model_name,
        object_id=object_id,
        extra_data=extra_data,
        ip_address=ip,
        user_agent=ua,
        timestamp=now(),
    )


def log_error(request, exception, extra_data=None):
    """Log an exception with full traceback to ErrorLog."""
    import traceback
    from core.models import ErrorLog  # imported here to avoid circular import

    tb = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    ip = get_client_ip(request) if request else None
    ua = request.META.get('HTTP_USER_AGENT', '') if request else None
    user = request.user if request and request.user.is_authenticated else None

    ErrorLog.objects.create(
        user=user,
        message=str(exception),
        traceback=tb,
        path=getattr(request, 'path', None),
        method=getattr(request, 'method', None),
        ip_address=ip,
        user_agent=ua,
        extra_data=extra_data or {},
    )
