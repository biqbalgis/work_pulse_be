"""
A memo that lives for one API request, for data one request reads many times but that almost never changes
(stat holidays). core.middleware.request_cache_middleware.RequestCacheMiddleware opens it at the start of each
request and drops it at the end, so nothing is ever shared between requests or users.

Outside a request (management commands, signals fired from scripts, helpers called directly in tests) there is
no memo and every call reads the database as before.
"""

from contextvars import ContextVar

_cache = ContextVar("workpulse_request_cache", default=None)


def begin():
    """Start an empty memo for the current request; pass the result to end()."""
    return _cache.set({})


def end(token):
    _cache.reset(token)


def clear():
    """Forget everything memoised in this request (e.g. after a holiday is added or removed)."""
    memo = _cache.get()
    if memo is not None:
        memo.clear()


def remember(key, compute):
    """compute() once per request for `key`; without a request, simply compute()."""
    memo = _cache.get()
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    return memo[key]
