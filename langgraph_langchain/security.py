"""Minimal API authentication, redaction, and sensitive-field governance."""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
OWNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class SessionIdentityError(ValueError):
    """Raised when a session or owner identity violates the platform policy."""


def validate_session_id(value: str) -> str:
    """Validate a session identifier as a safe single path segment."""
    if not isinstance(value, str) or not SESSION_ID_PATTERN.fullmatch(value):
        raise SessionIdentityError("session_id must match [A-Za-z0-9_-]{8,64}")
    return value


def validate_owner_id(value: str) -> str:
    """Validate an owner identity supplied by a trusted server-side source."""
    if not isinstance(value, str) or not OWNER_ID_PATTERN.fullmatch(value):
        raise SessionIdentityError("owner_id contains invalid characters")
    return value


def default_owner_id() -> str:
    """Return the deployment-bound owner for the authenticated API token.

    The value comes from operator configuration, never from request data. The
    default is deliberately stable for single-owner deployments and for legacy
    session migration.
    """
    return os.environ.get("DEEPANALYZE_API_OWNER_ID", "").strip() or "deployment-owner"

SENSITIVE_NAME = re.compile(r"(password|secret|token|api[_-]?key|authorization|email|phone|mobile|身份证|手机号|邮箱)", re.I)


def verify_bearer_token(header: str | None, configured_token: str) -> bool:
    """Return whether *header* carries the configured bearer token.

    An empty configured token never authenticates a request.  Deployment code
    decides whether an explicitly configured development mode may bypass auth.
    """
    if not configured_token:
        return False
    if not header or not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer ") :].strip()
    return bool(presented) and hmac.compare_digest(presented, configured_token)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***REDACTED***" if SENSITIVE_NAME.search(str(key)) else redact_sensitive(item)) for key,item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def sensitive_columns(columns: list[str]) -> list[str]:
    return [column for column in columns if SENSITIVE_NAME.search(str(column))]
