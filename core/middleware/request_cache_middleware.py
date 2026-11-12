from core.utils import request_cache


class RequestCacheMiddleware:
    """Gives each request its own short-lived memo (see core.utils.request_cache) and drops it afterwards."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request_cache.begin()
        try:
            return self.get_response(request)
        finally:
            request_cache.end(token)
