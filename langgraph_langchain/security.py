"""Minimal API authentication, redaction, and sensitive-field governance."""

from __future__ import annotations

import hmac
import re
from typing import Any

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
