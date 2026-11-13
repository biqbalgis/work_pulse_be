from core.utils import request_memo
from core.utils.workspace_utils import begin_request_cache, end_request_cache


class MembershipCacheMiddleware:
    """Opens the per-request caches: workspace memberships (core.utils.workspace_utils) and read-only lookups
    (core.utils.request_memo). Both are discarded when the request ends."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = begin_request_cache()
        memo_token = request_memo.begin()
        try:
            return self.get_response(request)
        finally:
            request_memo.end(memo_token)
            end_request_cache(token)
