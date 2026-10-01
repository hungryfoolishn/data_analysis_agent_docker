"""Runtime V10.0 tool registry tests."""

from __future__ import annotations

from langgraph_langchain.runtime.financial_agent import (
    FINANCIAL_TOOL_REGISTRY,
    TASK_TOOL_IDS,
    FinancialPlanTaskType,
    FinancialToolSpec,
)


def test_tool_registry_contains_all_planned_tools():
    expected_tools = {
        "company_resolver",
        "period_resolver",
        "data_coverage_checker",
        "financial_metric_engine",
        "risk_detector",
        "comparison_engine",
        "verification_engine",
        "finding_engine",
        "report_builder",
    }
    assert set(FINANCIAL_TOOL_REGISTRY) == expected_tools


def test_every_plan_task_type_has_registered_tool():
    assert set(TASK_TOOL_IDS) == set(FinancialPlanTaskType)
    for task_type, tool_id in TASK_TOOL_IDS.items():
        tool = FINANCIAL_TOOL_REGISTRY[tool_id]
        assert task_type in tool.supported_task_types
        assert tool.deterministic is True
        assert tool.input_schema
        assert tool.output_schema


def test_metric_engine_requires_verification():
    tool = FINANCIAL_TOOL_REGISTRY["financial_metric_engine"]
    assert isinstance(tool, FinancialToolSpec)
    assert tool.version == "v9.3.0"
    assert tool.requires_verification is True
    assert "metric_calculation" in tool.capabilities


def test_verification_and_report_tools_do_not_require_secondary_verification():
    assert FINANCIAL_TOOL_REGISTRY["verification_engine"].requires_verification is False
    assert FINANCIAL_TOOL_REGISTRY["report_builder"].requires_verification is False
