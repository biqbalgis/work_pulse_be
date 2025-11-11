from core.models import ActivityLog

def get_client_ip(request):
    """Extract real client IP from headers (supports proxies like Nginx)."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def log_activity(user, action, model_name, object_id=None, extra_data=None, request=None):
    """Log any user activity with optional request context."""
    ip = None
    ua = None
    if request is not None:
        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT')

    ActivityLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        model_name=model_name,
        object_id=object_id,
        extra_data=extra_data or {},
        ip_address=ip,
        user_agent=ua,
    )
