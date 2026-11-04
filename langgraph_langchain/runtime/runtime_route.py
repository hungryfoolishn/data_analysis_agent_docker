"""Explicit Runtime V2 selection and fallback policy."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}
_INFRASTRUCTURE_ACTIONS = {"blocked", "incomplete", "limit_reached"}


def _env_flag(
    values: Mapping[str, str], name: str, default: bool
) -> bool:
    raw = str(values.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in _TRUE


@dataclass(frozen=True)
class RuntimeSelectionPolicy:
    """Configuration contract for Runtime V2 selection and fallback."""

    enabled: bool = False
    fallback_enabled: bool = False
    strict_mode: bool = True

    @classmethod
    def from_env(
        cls,
        values: Mapping[str, str] | None = None,
    ) -> "RuntimeSelectionPolicy":
        env = dict(os.environ if values is None else values)
        return cls(
            enabled=_env_flag(env, "RUNTIME_V2_ENABLED", False),
            fallback_enabled=_env_flag(
                env, "RUNTIME_V2_FALLBACK_ENABLED", False
            ),
            strict_mode=_env_flag(env, "RUNTIME_V2_STRICT_MODE", True),
        )


@dataclass(frozen=True)
class RuntimeRouteDecision:
    selected_runtime: str
    fallback: bool
    reason: str
    error_type: str | None = None


def decide_runtime_route(
    *,
    policy: RuntimeSelectionPolicy,
    v2_completed: bool = False,
    v2_action: str | None = None,
    v2_has_result: bool = True,
    error_type: str | None = None,
) -> RuntimeRouteDecision:
    """Return an explicit routing decision; never hide business failures."""
    if not policy.enabled:
        return RuntimeRouteDecision(
            selected_runtime="legacy_v1",
            fallback=False,
            reason="runtime_v2_disabled",
        )
    if v2_completed:
        return RuntimeRouteDecision(
            selected_runtime="runtime_v2",
            fallback=False,
            reason="runtime_v2_completed",
        )

    infrastructure_failure = (
        v2_action in _INFRASTRUCTURE_ACTIONS and not v2_has_result
    )
    if (
        infrastructure_failure
        and policy.fallback_enabled
        and not policy.strict_mode
    ):
        return RuntimeRouteDecision(
            selected_runtime="legacy_v1",
            fallback=True,
            reason="runtime_v2_infrastructure_failure",
            error_type=error_type,
        )

    reason = (
        "runtime_v2_infrastructure_failure_strict_mode"
        if infrastructure_failure
        else "runtime_v2_task_failure"
    )
    return RuntimeRouteDecision(
        selected_runtime="runtime_v2",
        fallback=False,
        reason=reason,
        error_type=error_type,
    )


def log_runtime_route(
    logger: logging.Logger,
    decision: RuntimeRouteDecision,
    *,
    task_id: str | None = None,
    execution_id: str | None = None,
) -> None:
    """Emit non-sensitive route/fallback observability."""
    logger.info(
        "runtime_route selected=%s fallback=%s reason=%s error_type=%s "
        "task_id=%s execution_id=%s",
        decision.selected_runtime,
        decision.fallback,
        decision.reason,
        decision.error_type or "none",
        task_id or "none",
        execution_id or "none",
    )
