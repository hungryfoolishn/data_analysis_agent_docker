from __future__ import annotations

import logging

import pytest

from langgraph_langchain.config import RUNTIME_V2_FALLBACK_ENABLED
from langgraph_langchain.runtime.runtime_route import (
    RuntimeSelectionPolicy,
    decide_runtime_route,
    log_runtime_route,
)


def test_default_fallback_is_disabled():
    policy = RuntimeSelectionPolicy.from_env({})

    assert policy.enabled is False
    assert policy.fallback_enabled is False
    assert policy.strict_mode is True
    assert RUNTIME_V2_FALLBACK_ENABLED is False


def test_environment_can_explicitly_enable_v2_and_non_strict_fallback():
    policy = RuntimeSelectionPolicy.from_env(
        {
            "RUNTIME_V2_ENABLED": "true",
            "RUNTIME_V2_FALLBACK_ENABLED": "true",
            "RUNTIME_V2_STRICT_MODE": "false",
        }
    )

    assert policy.enabled is True
    assert policy.fallback_enabled is True
    assert policy.strict_mode is False


def test_v2_completion_routes_to_runtime_v2_without_fallback():
    policy = RuntimeSelectionPolicy(enabled=True)

    decision = decide_runtime_route(policy=policy, v2_completed=True)

    assert decision.selected_runtime == "runtime_v2"
    assert decision.fallback is False


def test_business_failure_never_falls_back_even_when_fallback_enabled():
    policy = RuntimeSelectionPolicy(
        enabled=True,
        fallback_enabled=True,
        strict_mode=False,
    )

    decision = decide_runtime_route(
        policy=policy,
        v2_action="failed",
        v2_has_result=True,
        error_type="ToolExecutionError",
    )

    assert decision.selected_runtime == "runtime_v2"
    assert decision.fallback is False
    assert decision.reason == "runtime_v2_task_failure"


def test_strict_mode_blocks_infrastructure_fallback():
    policy = RuntimeSelectionPolicy(
        enabled=True,
        fallback_enabled=True,
        strict_mode=True,
    )

    decision = decide_runtime_route(
        policy=policy,
        v2_action="blocked",
        v2_has_result=False,
        error_type="RuntimeInfrastructureError",
    )

    assert decision.selected_runtime == "runtime_v2"
    assert decision.fallback is False
    assert decision.reason == "runtime_v2_infrastructure_failure_strict_mode"


def test_explicit_non_strict_policy_allows_infrastructure_fallback():
    policy = RuntimeSelectionPolicy(
        enabled=True,
        fallback_enabled=True,
        strict_mode=False,
    )

    decision = decide_runtime_route(
        policy=policy,
        v2_action="limit_reached",
        v2_has_result=False,
        error_type="RuntimeInfrastructureError",
    )

    assert decision.selected_runtime == "legacy_v1"
    assert decision.fallback is True
    assert decision.reason == "runtime_v2_infrastructure_failure"


def test_disabled_v2_routes_to_legacy_without_fallback():
    policy = RuntimeSelectionPolicy(enabled=False)

    decision = decide_runtime_route(policy=policy)

    assert decision.selected_runtime == "legacy_v1"
    assert decision.fallback is False


def test_route_logging_contains_ids_but_not_sensitive_data(caplog):
    policy = RuntimeSelectionPolicy(
        enabled=True,
        fallback_enabled=True,
        strict_mode=False,
    )
    decision = decide_runtime_route(
        policy=policy,
        v2_action="blocked",
        v2_has_result=False,
    )

    with caplog.at_level(logging.INFO, logger="runtime.route.test"):
        log_runtime_route(
            logging.getLogger("runtime.route.test"),
            decision,
            task_id="task_1",
            execution_id="exec_1",
        )

    assert "runtime_route" in caplog.text
    assert "task_1" in caplog.text
    assert "exec_1" in caplog.text
    assert "secret" not in caplog.text.lower()
