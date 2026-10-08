"""Runtime V10.2 executable tool registry tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial_agent import (
    FINANCIAL_TOOL_REGISTRY,
    ExecutableFinancialTool,
    FinancialToolExecutionRegistry,
    FinancialToolRegistryError,
    FinancialToolSpec,
    FinancialPlanTaskType,
    ToolExecutionStatus,
    UnknownFinancialToolError,
)


def _tool_spec(tool_id: str = "test_tool") -> FinancialToolSpec:
    return FinancialToolSpec(
        tool_id=tool_id,
        name="Test Tool",
        version="v10.2.0",
        input_schema=f"{tool_id}_input_v1",
        output_schema=f"{tool_id}_output_v1",
        capabilities=["test"],
        supported_task_types=[FinancialPlanTaskType.RESOLVE_COMPANIES],
    )


def _executable_tool(
    executor=None,
    tool_id: str = "test_tool",
) -> ExecutableFinancialTool:
    return ExecutableFinancialTool(
        spec=_tool_spec(tool_id),
        executor=executor or (lambda payload: {"echo": payload}),
    )


def test_executable_tool_registry_binds_and_executes_spec():
    registry = FinancialToolExecutionRegistry([
        _executable_tool(
            executor=lambda payload: {
                "company_id": payload["company_id"],
                "doubled": payload["value"] * 2,
            }
        )
    ])

    result = registry.execute(
        "test_tool",
        {"company_id": "600519", "value": 21},
        metadata={"source": "test"},
        request_id="request_001",
    )

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert result.error is None
    assert result.output == {"company_id": "600519", "doubled": 42}
    assert result.request_id == "request_001"
    assert result.metadata == {"source": "test"}
    assert result.started_at <= result.finished_at
    assert result.duration_ms >= 0


def test_registry_can_be_built_from_static_specs_and_executors():
    executors = {
        "financial_metric_engine": lambda payload: {
            "metric_id": payload["metric_id"],
            "status": "SUCCEEDED",
        }
    }
    registry = FinancialToolExecutionRegistry.from_specs(
        FINANCIAL_TOOL_REGISTRY,
        executors,
    )

    tool = registry.get("financial_metric_engine")
    assert tool.spec.tool_id == "financial_metric_engine"
    assert tool.spec.version == "v9.3.0"

    result = registry.execute(
        "financial_metric_engine",
        {"metric_id": "revenue"},
    )
    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert result.output == {"metric_id": "revenue", "status": "SUCCEEDED"}


def test_registry_rejects_duplicate_tool_id():
    registry = FinancialToolExecutionRegistry([_executable_tool()])

    with pytest.raises(FinancialToolRegistryError, match="Duplicate executable tool ID"):
        registry.register(_executable_tool())


def test_registry_rejects_non_callable_executor():
    with pytest.raises(FinancialToolRegistryError, match="must be callable"):
        FinancialToolExecutionRegistry([
            ExecutableFinancialTool(spec=_tool_spec(), executor="not-callable")
        ])


def test_registry_rejects_binding_unknown_static_tool():
    with pytest.raises(
        FinancialToolRegistryError,
        match="Cannot bind unknown tool ID",
    ):
        FinancialToolExecutionRegistry.from_specs(
            FINANCIAL_TOOL_REGISTRY,
            {"not_a_real_tool": lambda payload: payload},
        )


def test_registry_raises_unknown_tool_error():
    registry = FinancialToolExecutionRegistry([_executable_tool()])

    with pytest.raises(UnknownFinancialToolError):
        registry.execute("missing_tool", {})

    with pytest.raises(UnknownFinancialToolError):
        registry.get("missing_tool")


def test_executor_failure_is_preserved_as_failed_result():
    def failing_executor(payload):
        raise RuntimeError("metric engine unavailable")

    registry = FinancialToolExecutionRegistry([
        _executable_tool(executor=failing_executor)
    ])

    result = registry.execute("test_tool", {}, request_id="request_failed")

    assert result.status == ToolExecutionStatus.FAILED
    assert result.output is None
    assert result.error == "RuntimeError: metric engine unavailable"
    assert result.request_id == "request_failed"


def test_registry_lists_tools_in_stable_order():
    registry = FinancialToolExecutionRegistry([
        _executable_tool(tool_id="z_tool"),
        _executable_tool(tool_id="a_tool"),
        _executable_tool(tool_id="m_tool"),
    ])

    assert [tool.spec.tool_id for tool in registry.list_tools()] == [
        "a_tool",
        "m_tool",
        "z_tool",
    ]
