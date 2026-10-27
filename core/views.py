from datetime import date

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils import pay_rules
from core.utils.workspace_utils import get_user_primary_workspace


class PayRulesView(APIView):
    """GET /api/core/pay-rules/?year=2026 — the pay rules the app splits and prices hours by, and that year's stat
    holidays (Alberta's plus the workspace's own), so forms can show Regular / Overtime / Double time as people
    type."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            year = int(request.query_params.get("year") or date.today().year)
        except ValueError:
            return Response({"error": "year must be a number"}, status=400)
        workspace = get_user_primary_workspace(request.user)
        holidays = pay_rules.stat_holidays([year], workspace.id if workspace else None)
        return Response({
            "daily_rt_hours": float(pay_rules.DAILY_RT_HOURS),
            "ot_multiplier": float(pay_rules.OT_MULTIPLIER),
            "dt_multiplier": float(pay_rules.DT_MULTIPLIER),
            "stat_holidays": [{"date": day.isoformat(), "name": name} for day, name in sorted(holidays.items())],
        })
