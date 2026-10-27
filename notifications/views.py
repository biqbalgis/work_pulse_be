from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification

MAX_LIMIT = 100


def _serialize(n):
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": n.title,
        "message": n.message,
        "link": n.link,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
        "actor_name": (n.actor.get_full_name() or n.actor.email) if n.actor_id else "",
    }


def _unread_count(user):
    return Notification.objects.filter(recipient=user, is_read=False).count()


class NotificationListView(APIView):
    """GET /api/notifications/?limit=20&unread=1 — the signed-in user's latest notifications, and how many are
    unread."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), MAX_LIMIT))
        except ValueError:
            limit = 20
        rows = Notification.objects.filter(recipient=request.user)
        if request.query_params.get("unread") in ("1", "true"):
            rows = rows.filter(is_read=False)
        rows = rows.select_related("actor")[:limit]
        return Response({"results": [_serialize(n) for n in rows], "unread_count": _unread_count(request.user)})


class NotificationReadView(APIView):
    """POST /api/notifications/<id>/read/ — mark one read; {"read": false} marks it unread again."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = Notification.objects.filter(recipient=request.user, pk=pk).select_related("actor").first()
        if not notification:
            return Response({"error": "Notification not found."}, status=404)
        read = request.data.get("read", True) not in (False, "false", "0", 0)
        notification.is_read = read
        notification.read_at = timezone.now() if read else None
        notification.save(update_fields=["is_read", "read_at"])
        return Response({**_serialize(notification), "unread_count": _unread_count(request.user)})


class NotificationReadAllView(APIView):
    """POST /api/notifications/read-all/ — mark every unread notification read."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now())
        return Response({"updated": updated, "unread_count": 0})
