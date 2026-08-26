"""API error classification for smart failover and recovery.

Adapted from hermes-agent's agent/error_classifier.py, simplified for
our DeepSeek/OpenAI-compatible provider. Provides a structured taxonomy
of API errors and a classification pipeline that determines the correct
recovery action (retry with backoff, abort, or retry with narrower scope).

Usage in the agent loop:
    classified = classify_api_error(exc, status_code=500)
    if classified.retryable:
        delay = jittered_backoff(retry_count)
        await asyncio.sleep(delay)
        continue  # retry
    else:
        yield error to user
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Error taxonomy ──────────────────────────────────────────────────────────

class FailoverReason(enum.Enum):
    """Why an API call failed — determines recovery strategy."""

    auth = "auth"                        # 401/403 — check API key
    billing = "billing"                  # 402 — quota exhausted
    rate_limit = "rate_limit"            # 429 — backoff then retry
    overloaded = "overloaded"            # 503/529 — provider overloaded
    server_error = "server_error"        # 500/502 — internal server error
    timeout = "timeout"                  # Connection/read timeout
    context_overflow = "context_overflow"  # Context too large
    model_not_found = "model_not_found"  # 404 — invalid model
    format_error = "format_error"        # 400 — bad request
    unknown = "unknown"                  # Unclassifiable


# ── Classification result ───────────────────────────────────────────────────

@dataclass
class ClassifiedError:
    """Structured classification of an API error with recovery hints."""

    reason: FailoverReason
    status_code: Optional[int] = None
    message: str = ""

    # Recovery action hints
    retryable: bool = True
    should_compress: bool = False   # For context_overflow
    max_retries: int = 3

    @property
    def is_retryable(self) -> bool:
        return self.retryable


# ── Provider-specific patterns ──────────────────────────────────────────────

_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "credit balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
]

_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "concurrency limit",
    "concurrent request",
]

_OVERLOADED_PATTERNS = [
    "overloaded",
    "overloaded_error",
    "service temporarily unavailable",
    "capacity",
    "try again later",
    "server is busy",
]

_CONTEXT_OVERFLOW_PATTERNS = [
    "context_length_exceeded",
    "maximum context length",
    "too many tokens",
    "token limit",
    "reduce the length",
    "exceeds the maximum",
]


# ── Classification pipeline ────────────────────────────────────────────────

def classify_api_error(
    exception: Exception,
    status_code: Optional[int] = None,
    provider: str = "",
) -> ClassifiedError:
    """Classify an API error and determine recovery strategy.

    Priority-ordered classification:
      1. Status code (if available)
      2. Error message pattern matching
      3. Exception type
      4. Catch-all unknown
    """
    error_msg = str(exception).lower()
    exc_type = type(exception).__name__

    # ── Status code classification (highest priority) ───────────────────
    if status_code:
        if status_code in (401, 403):
            return ClassifiedError(
                reason=FailoverReason.auth,
                status_code=status_code,
                message=str(exception),
                retryable=False,
            )

        if status_code == 402:
            return ClassifiedError(
                reason=FailoverReason.billing,
                status_code=status_code,
                message=str(exception),
                retryable=False,
            )

        if status_code == 429:
            # Disambiguate: billing vs rate limit
            if any(p in error_msg for p in _BILLING_PATTERNS):
                return ClassifiedError(
                    reason=FailoverReason.billing,
                    status_code=status_code,
                    message=str(exception),
                    retryable=False,
                )
            return ClassifiedError(
                reason=FailoverReason.rate_limit,
                status_code=status_code,
                message=str(exception),
                retryable=True,
                max_retries=5,
            )

        if status_code in (503, 529):
            return ClassifiedError(
                reason=FailoverReason.overloaded,
                status_code=status_code,
                message=str(exception),
                retryable=True,
                max_retries=5,
            )

        if status_code in (500, 502):
            return ClassifiedError(
                reason=FailoverReason.server_error,
                status_code=status_code,
                message=str(exception),
                retryable=True,
                max_retries=3,
            )

        if status_code == 404:
            return ClassifiedError(
                reason=FailoverReason.model_not_found,
                status_code=status_code,
                message=str(exception),
                retryable=False,
            )

        if status_code == 400:
            # Check for context overflow in 400
            if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
                return ClassifiedError(
                    reason=FailoverReason.context_overflow,
                    status_code=status_code,
                    message=str(exception),
                    retryable=False,
                    should_compress=True,
                )
            return ClassifiedError(
                reason=FailoverReason.format_error,
                status_code=status_code,
                message=str(exception),
                retryable=False,
            )

    # ── Message pattern matching ───────────────────────────────────────
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.billing,
            status_code=status_code,
            message=str(exception),
            retryable=False,
        )

    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.rate_limit,
            status_code=status_code,
            message=str(exception),
            retryable=True,
            max_retries=5,
        )

    if any(p in error_msg for p in _OVERLOADED_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.overloaded,
            status_code=status_code,
            message=str(exception),
            retryable=True,
            max_retries=5,
        )

    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.context_overflow,
            status_code=status_code,
            message=str(exception),
            retryable=False,
            should_compress=True,
        )

    # ── Exception type classification ──────────────────────────────────
    timeout_types = {"TimeoutError", "ConnectTimeout", "ReadTimeout",
                     "ConnectTimeoutError", "asyncio.TimeoutError"}
    if exc_type in timeout_types or "timeout" in error_msg:
        return ClassifiedError(
            reason=FailoverReason.timeout,
            status_code=status_code,
            message=str(exception),
            retryable=True,
            max_retries=3,
        )

    connection_types = {"ConnectionError", "ConnectError",
                        "ConnectionResetError", "BrokenPipeError"}
    if exc_type in connection_types:
        return ClassifiedError(
            reason=FailoverReason.server_error,
            status_code=status_code,
            message=str(exception),
            retryable=True,
            max_retries=3,
        )

    # ── Catch-all ───────────────────────────────────────────────────────
    return ClassifiedError(
        reason=FailoverReason.unknown,
        status_code=status_code,
        message=str(exception),
        retryable=True,
        max_retries=2,
    )
