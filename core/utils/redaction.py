"""
Redaction of request data before it is written to the audit / error logs.

Anything that looks like a credential is replaced with "[REDACTED]" so passwords and tokens never reach the
ActivityLog / ErrorLog tables. Matching is case-insensitive and by substring ("old_password", "newPassword",
"Authorization" ...), because the set of field names that carry secrets is open-ended.
"""

import json
from urllib.parse import parse_qsl

REDACTED = "[REDACTED]"

# Substrings: any key containing one of these is a secret.
_SENSITIVE_FRAGMENTS = ("pass", "token", "secret", "authorization")
# Exact keys that are secrets but too short/common to match as substrings.
_SENSITIVE_EXACT = {"refresh", "access", "pwd", "otp"}

# Bodies larger than this are summarised instead of stored (keeps the log tables and request path small).
MAX_LOGGED_BODY_BYTES = 256 * 1024


def _is_sensitive_key(key):
    lowered = str(key).lower()
    return lowered in _SENSITIVE_EXACT or any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)


def redact_sensitive_data(data):
    """Recursively redact sensitive keys in dicts / lists. Returns a new structure; the input is not modified."""
    if isinstance(data, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_sensitive_data(value))
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    return data


def safe_body_for_log(request):
    """
    A log-safe version of the request body: redacted JSON, or redacted form fields, or — for anything else
    (multipart uploads, unparseable or huge bodies) — only the content type and size, never the raw bytes.
    The body is not read at all for multipart requests.
    """
    content_type = (request.META.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
    try:
        declared_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared_length = 0

    def summary():
        return {"unlogged": True, "content_type": content_type or None, "length": declared_length}

    if declared_length == 0:
        return None
    if content_type.startswith("multipart/") or declared_length > MAX_LOGGED_BODY_BYTES:
        return summary()

    try:
        raw = request.body
    except Exception:  # RawPostDataException / RequestDataTooBig: the body was already consumed or is too big
        return summary()
    if not raw:
        return None

    try:
        text = raw.decode("utf-8")
        if content_type == "application/x-www-form-urlencoded":
            return redact_sensitive_data(dict(parse_qsl(text, keep_blank_values=True)))
        return redact_sensitive_data(json.loads(text))
    except Exception:
        return summary()
