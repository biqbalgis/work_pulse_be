"""
Per-request memo for read-only lookups that a request repeats (e.g. every row of a field ticket looks up the same
project, asset and job title).

Active only while a request is running (core.middleware.membership_cache_middleware opens and closes it); anywhere
else — management commands, scripts, tests calling helpers directly — `remember` just calls the loader, so nothing
is ever cached across requests or processes.

Only memoise rows the request itself never changes (a project, an asset, a rate, a user). Anything the request
writes must not go through here.
"""

from contextvars import ContextVar

_memo = ContextVar("request_memo", default=None)


def begin():
    return _memo.set({})


def end(token):
    _memo.reset(token)


def remember(key, loader):
    """Return the memoised value for `key`, calling `loader()` once per request (the result may be None)."""
    store = _memo.get()
    if store is None:
        return loader()
    if key not in store:
        store[key] = loader()
    return store[key]


def prefill(key, value):
    """Seed a value found by a bulk query so later `remember(key, ...)` calls find it."""
    store = _memo.get()
    if store is not None:
        store[key] = value
