"""
Every error the API returns carries one readable sentence in "error" (what the app shows people), whatever raised
it, alongside the original details (field errors, "detail", ...) for code that reads them:

  * DRF validation errors {"email": ["user with this email already exists."]}  ->  "Email: user with this email
    already exists."  (and the field errors are kept)
  * a list of errors (e.g. one per field ticket row)  ->  {"error": "Row 1: ...", "errors": [...]}
  * a duplicate the database refused (IntegrityError)  ->  400 "That job code already exists." instead of a crash
  * anything unexpected  ->  500 "Something went wrong on the server..." (logged) instead of an HTML error page
"""

import logging
import re

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

# Labels that say what kind of error it was, not what went wrong: never shown on their own.
GENERIC_LABELS = {"validation error", "invalid value", "not found", "error", "bad request", "invalid input."}

# How a database column reads in a message.
FIELD_LABELS = {
    "lem_number": "LEM number",
    "job_code": "job code",
    "email": "email",
    "username": "username",
    "ticket_number": "ticket number",
    "invoice_number": "invoice number",
}
SERVER_ERROR = "Something went wrong on the server. Please try again, or contact an admin if it keeps happening."


def _label(field):
    return FIELD_LABELS.get(field, field.replace("_", " "))


def _text(value):
    """Flatten an error value (string, list, dict, ErrorDetail) into one readable sentence."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [_text(v) for v in value]
        if all(isinstance(v, dict) for v in value):  # one error dict per row: number the rows that failed
            return " ".join(f"Row {i}: {p}" for i, p in enumerate(parts, start=1) if p)
        return " ".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in ("error", "detail", "message"):
            text = _text(value.get(key))
            if text and text.lower() not in GENERIC_LABELS:
                return text
        parts = []
        for field, message in value.items():
            if field in ("error", "detail", "message", "code", "status_code"):
                continue
            text = _text(message)
            if not text:
                continue
            if field in ("non_field_errors", "__all__"):
                parts.append(text)
            else:
                label = _label(field)
                parts.append(f"{label[:1].upper()}{label[1:]}: {text}")
        return " ".join(parts)
    return str(value).strip()


def readable(data, fallback="Something isn't right with that request."):
    text = _text(data)
    return text if text and text.lower() not in GENERIC_LABELS else fallback


def _duplicate_message(exc):
    """'That job code already exists.' from a unique-constraint failure (Postgres or SQLite wording)."""
    message = str(exc)
    fields = []
    match = re.search(r"Key \(([^)]+)\)=", message)  # Postgres: DETAIL: Key (workspace_id, job_code)=(...) already exists.
    if match:
        fields = [f.strip() for f in match.group(1).split(",")]
    else:
        match = re.search(r"UNIQUE constraint failed: (.+)", message)  # SQLite: ... project.workspace_id, project.job_code
        if match:
            fields = [part.strip().split(".")[-1] for part in match.group(1).split(",")]
    meaningful = [f for f in fields if not f.endswith("_id") and f not in ("id", "workspace", "project")]
    if meaningful:
        return f"That {' and '.join(_label(f) for f in meaningful)} already exists."
    if "unique" in message.lower() or "duplicate" in message.lower():
        return "That already exists."
    if "not null" in message.lower() or "null value" in message.lower():
        return "Something required is missing."
    return "That couldn't be saved because it conflicts with existing data."


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        if isinstance(exc, ValidationError):
            if hasattr(exc, "message_dict"):
                detail = exc.message_dict
            elif hasattr(exc, "messages"):
                detail = exc.messages
            else:
                detail = str(exc)
            response = Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(exc, ObjectDoesNotExist):
            response = Response({"detail": "The requested item was not found."}, status=status.HTTP_404_NOT_FOUND)
        elif isinstance(exc, IntegrityError):
            response = Response({"error": _duplicate_message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(exc, ValueError):
            # Often happens with UUID conversion or date parsing
            response = Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            logger.exception("Unhandled error in %s", context.get("view").__class__.__name__ if context.get("view") else "API")
            response = Response({"error": SERVER_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # One readable sentence in "error", the original details kept beside it.
    data = response.data
    if isinstance(data, (list, tuple)):
        response.data = {"error": readable(data), "errors": data}
    elif isinstance(data, dict):
        current = data.get("error")
        if not (isinstance(current, str) and current.strip() and current.strip().lower() not in GENERIC_LABELS):
            response.data = {**data, "error": readable({k: v for k, v in data.items() if k != "error"})}
    else:
        response.data = {"error": readable(data)}
    return response
