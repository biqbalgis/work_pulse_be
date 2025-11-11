import logging
from django.utils import timezone
from core.models import ActivityLog

logger = logging.getLogger(__name__)

def log_activity(user, action, model_name, object_id=None, extra_data=None):
    ActivityLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        extra_data=extra_data or {},
        timestamp=timezone.now()
    )
    logger.info(f"{user} performed {action} on {model_name} (ID: {object_id})")
