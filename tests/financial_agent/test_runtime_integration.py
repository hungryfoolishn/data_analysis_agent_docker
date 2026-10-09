"""Runtime V10.2 integration tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)
from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
)
from langgraph_langchain.runtime.financial_agent import (
    ExecutionPlanBuilder,
    ExecutionPlanStatus,
    ExecutionStatus,
    FinancialSemanticResolver,
    FinancialRuntimeExecutor,
    FinancialTaskUnderstandingBuilder,
    RuntimeExecutionStatus,
    RuntimeIntegrationError,
    VerificationStatus,
)


PERIODS = ["2021", "2022", "2023", "2024"]


def _company() -> Company:
    return Company(
        company_id="600519",
        stock_code="600519",
        company_name="贵州茅台",
        exchange="SSE",
        industry_id="baijiu",
    )


def _income(period: str, **overrides) -> IncomeStatement:
    values = {
        "company_id": "600519",
        "period": period,
        "revenue": 100.0 + int(period),
        "cost_of_revenue": 30.0,
        "gross_profit": 70.0,
        "operating_profit": 40.0,
        "net_profit": 25.0,
        "net_profit_attributable": 24.0,
        "source_id": f"income_{period}",
    }
    values.update(overrides)
    return IncomeStatement(**values)


def _balance(period: str) -> BalanceSheetStatement:
    return BalanceSheetStatement(
        company_id="600519",
        period=period,
        total_assets=500.0,
        total_liabilities=200.0,
        total_equity=300.0,
        cash=150.0,
        accounts_receivable=20.0,
        inventory=30.0,
        fixed_assets=100.0,
        short_term_debt=50.0,
        long_term_debt=50.0,
        current_assets=200.0,
        current_liabilities=100.0,
        source_id=f"balance_{period}",
    )


def _cash(period: str) -> CashFlowStatement:
    return CashFlowStatement(
        company_id="600519",
        period=period,
        operating_cash_flow=35.0,
        investing_cash_flow=-10.0,
        financing_cash_flow=-5.0,
        capital_expenditure=12.0,
        free_cash_flow=23.0,
        source_id=f"cash_{period}",
    )


def _service(*, income_overrides=None, cash_overrides=None) -> FinancialDataService:
    company = _company()
    income_overrides = income_overrides or {}
    cash_overrides = cash_overrides or {}
    return FinancialDataService(
        companies=[company],
        income_statements=[
            _income(period, **income_overrides.get(period, {}))
            for period in PERIODS
        ],
        balance_sheets=[_balance(period) for period in PERIODS],
        cash_flows=[
            _cash(period, **cash_overrides.get(period, {}))
            for period in PERIODS
        ],
    )


def _understanding(question: str):
    return FinancialTaskUnderstandingBuilder(["贵州茅台"]).build(question)


def _build_plan(question: str):
    understanding = _understanding(question)
    resolver = FinancialSemanticResolver(_service())
    return understanding, resolver, ExecutionPlanBuilder(
        semantic_resolver=resolver
    ).build(understanding, plan_id="plan_runtime_test")


def _assert_result_accounting(result):
    assert (
        result.succeeded_count
        + result.unavailable_count
        + result.failed_count
        == len(result.task_results)
    )


def test_runtime_executor_completes_plan_and_preserves_evidence():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入和净利润"
    )
    workflow = FinancialAnalysisWorkflow(_service())
    result = FinancialRuntimeExecutor(workflow).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert execution_plan.status == ExecutionPlanStatus.SUCCEEDED
    assert result.succeeded_count == len(plan_tasks := execution_plan.tasks)
    assert result.unavailable_count == 0
    assert result.failed_count == 0
    assert result.report_markdown
    _assert_result_accounting(result)
    assert result.metadata["evidence_count"] > 0
    assert result.metadata["finding_count"] > 0

    for task_result in result.task_results:
        assert task_result.status == ExecutionStatus.SUCCEEDED
        assert task_result.verification_status.value == "PASSED"
        assert task_result.calculation_id
        assert task_result.verification_id
        assert task_result.evidence_id
        assert len(task_result.output_refs) == 3

    revenue_2024 = next(
        item for item in result.task_results
        if item.period == "2024" and item.metric_id == "revenue"
    )
    assert revenue_2024.company_id == "600519"

    assert resolver.resolve(understanding).ready_for_planning is True
    assert len(plan_tasks) == 12


def test_runtime_executor_maps_first_missing_previous_period_to_unavailable():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2021-2024 年收入和净利润"
    )
    workflow = FinancialAnalysisWorkflow(_service())
    result = FinancialRuntimeExecutor(workflow).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.PARTIAL
    assert execution_plan.status == ExecutionPlanStatus.PARTIAL
    assert result.succeeded_count == 14
    assert result.unavailable_count == 2
    assert result.failed_count == 0
    _assert_result_accounting(result)

    unavailable = [
        item for item in result.task_results
        if item.status == ExecutionStatus.UNAVAILABLE
    ]
    assert {item.metric_id for item in unavailable} == {
        "revenue_growth", "net_profit_growth"
    }
    assert all(item.period == "2021" for item in unavailable)
    assert all(
        item.status_reason in {
            "MISSING_PREVIOUS_DATA", "growth_previous_period_missing"
        }
        for item in unavailable
    )

    succeeded = [
        item for item in result.task_results
        if item.status == ExecutionStatus.SUCCEEDED
    ]
    assert len(succeeded) == result.succeeded_count
    assert all(item.evidence_id for item in succeeded)
    assert result.metadata["evidence_count"] >= len(succeeded)


def test_runtime_executor_rejects_non_pending_plan():
    understanding, resolver, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入"
    )
    execution_plan.status = ExecutionPlanStatus.SUCCEEDED

    with pytest.raises(RuntimeIntegrationError, match="must be PENDING"):
        FinancialRuntimeExecutor(FinancialAnalysisWorkflow(_service())).execute_plan(
            execution_plan,
            understanding=understanding,
        )
"""Additional V10.2.1 runtime hardening tests."""


import pytest

from langgraph_langchain.runtime.financial.models import FinancialQuery
from langgraph_langchain.runtime.financial_agent import (
    ExecutableFinancialTool,
    ExecutionPlanStatus,
    ExecutionStatus,
    FinancialToolExecutionRegistry,
    FinancialToolSpec,
    RuntimeExecutionStatus,
)
import langgraph_langchain.runtime.financial_agent.runtime_integration as runtime_integration_module


WORKFLOW_TOOL_ID = runtime_integration_module.WORKFLOW_TOOL_ID


def _registry_spy(workflow, calls):
    def execute_workflow(payload):
        calls.append(payload)
        return workflow.run(FinancialQuery.model_validate(payload["query"]))

    spec = FinancialToolSpec(
        tool_id=WORKFLOW_TOOL_ID,
        name="Financial Analysis Workflow Spy",
        version="v10.2.1",
        input_schema="financial_workflow_runtime_input_v1",
        output_schema="financial_workflow_runtime_output_v1",
        capabilities=["financial_analysis", "verification", "evidence"],
        deterministic=True,
    )
    return FinancialToolExecutionRegistry([
        ExecutableFinancialTool(spec=spec, executor=execute_workflow),
    ])


def test_runtime_executes_tool_through_registry():
    calls = []
    workflow = FinancialAnalysisWorkflow(_service())
    understanding, _, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入和净利润"
    )
    result = FinancialRuntimeExecutor(
        workflow,
        tool_registry=_registry_spy(workflow, calls),
    ).execute_plan(execution_plan, understanding=understanding)

    assert len(calls) == 1
    assert calls[0]["query"]["company_names"] == ["贵州茅台"]
    assert result.metadata["workflow_tool_id"] == WORKFLOW_TOOL_ID
    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert execution_plan.status == ExecutionPlanStatus.SUCCEEDED


def test_runtime_missing_tool_returns_structured_failure_result():
    understanding = _understanding("分析贵州茅台 2021-2024 年收入和净利润")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_missing_workflow_tool")
    unavailable_task_ids = {
        task.task_id
        for task in execution_plan.tasks
        if task.status == ExecutionStatus.UNAVAILABLE
    }
    assert unavailable_task_ids

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_service()),
        tool_registry=FinancialToolExecutionRegistry([]),
    ).execute_plan(execution_plan, understanding=understanding)

    error = f"Unknown workflow tool: {WORKFLOW_TOOL_ID}"
    assert result.status == RuntimeExecutionStatus.FAILED
    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert execution_plan.finished_at is not None
    assert result.metadata["error"] == error
    assert result.metadata["tool_status"] == "UNKNOWN"
    assert result.metadata["tool_error"]
    assert len(result.task_results) == len(execution_plan.tasks)
    assert result.succeeded_count == 0
    assert result.unavailable_count == len(unavailable_task_ids)
    assert result.failed_count == len(execution_plan.tasks) - len(unavailable_task_ids)
    _assert_result_accounting(result)

    result_by_task_id = {item.execution_task_id: item for item in result.task_results}
    for task in execution_plan.tasks:
        task_result = result_by_task_id[task.task_id]
        expected_status = (
            ExecutionStatus.UNAVAILABLE
            if task.task_id in unavailable_task_ids
            else ExecutionStatus.FAILED
        )
        expected_verification = (
            VerificationStatus.NOT_REQUIRED
            if task.task_id in unavailable_task_ids
            else VerificationStatus.FAILED
        )
        assert task.status == expected_status
        assert task.verification_status == expected_verification.value
        if expected_status == ExecutionStatus.FAILED:
            assert task.status_reason == error
        assert task_result.status == expected_status
        assert task_result.verification_status == expected_verification
        expected_error = (
            task.status_reason
            if expected_status == ExecutionStatus.UNAVAILABLE
            else error
        )
        assert task_result.error == expected_error


def test_runtime_workflow_failure_preserves_unavailable_task_semantics():
    class ExplodingWorkflow:
        def run(self, query):
            raise RuntimeError("workflow exploded")

    understanding = _understanding("分析贵州茅台 2021-2024 年收入和净利润")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_workflow_failure_details")
    unavailable_count = len(execution_plan.unavailable_tasks)
    assert unavailable_count

    result = FinancialRuntimeExecutor(ExplodingWorkflow()).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    error = "workflow tool failed"
    assert result.status == RuntimeExecutionStatus.FAILED
    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert len(result.task_results) == len(execution_plan.tasks)
    assert result.succeeded_count == 0
    assert result.unavailable_count == unavailable_count
    assert result.failed_count == len(execution_plan.tasks) - unavailable_count
    _assert_result_accounting(result)
    assert {
        item.execution_task_id
        for item in result.task_results
        if item.status == ExecutionStatus.UNAVAILABLE
    } == {
        task.task_id for task in execution_plan.unavailable_tasks
    }
    assert all(
        item.status_reason == error
        for item in result.task_results
        if item.status == ExecutionStatus.FAILED
    )


def test_runtime_unexpected_tool_output_returns_structured_failure_result():
    spec = FinancialToolSpec(
        tool_id=WORKFLOW_TOOL_ID,
        name="Invalid Output Workflow",
        version="v10.2.1",
        input_schema="financial_workflow_runtime_input_v1",
        output_schema="financial_workflow_runtime_output_v1",
    )
    registry = FinancialToolExecutionRegistry([
        ExecutableFinancialTool(
            spec=spec,
            executor=lambda payload: {"unexpected": True},
        ),
    ])
    understanding = _understanding("分析贵州茅台 2022-2024 年收入")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_invalid_workflow_output")

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(_service()),
        tool_registry=registry,
    ).execute_plan(execution_plan, understanding=understanding)

    error = "Registered workflow tool returned an unexpected result type."
    assert result.status == RuntimeExecutionStatus.FAILED
    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert result.metadata["error"] == error
    assert result.metadata["tool_status"] == "SUCCEEDED"
    assert len(result.task_results) == len(execution_plan.tasks)
    assert result.succeeded_count == 0
    assert result.unavailable_count == 0
    assert result.failed_count == len(execution_plan.tasks)
    _assert_result_accounting(result)
    assert all(
        task.status == ExecutionStatus.FAILED
        for task in execution_plan.tasks
    )


def test_runtime_marks_plan_failed_when_workflow_raises():
    class ExplodingWorkflow:
        def run(self, query):
            raise RuntimeError("workflow exploded")

    understanding = _understanding("分析贵州茅台 2022-2024 年收入")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_exploding_workflow")
    result = FinancialRuntimeExecutor(ExplodingWorkflow()).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.FAILED
    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert execution_plan.finished_at is not None
    assert execution_plan.status.value != "RUNNING"
    assert result.metadata["error"] == "workflow tool failed"
    assert result.metadata["tool_status"] == "FAILED"


def test_runtime_handles_all_tasks_unavailable():
    service = _service(
        income_overrides={
            period: {"revenue": None}
            for period in PERIODS
        },
    )
    understanding = _understanding("分析贵州茅台 2021-2024 年收入")
    resolver = FinancialSemanticResolver(service)
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=resolver
    ).build(understanding, plan_id="plan_all_unavailable")
    assert execution_plan.pending_tasks == []
    assert execution_plan.unavailable_tasks

    result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(service)
    ).execute_plan(execution_plan, understanding=understanding)

    assert result.status == RuntimeExecutionStatus.BLOCKED
    assert execution_plan.status == ExecutionPlanStatus.BLOCKED
    assert result.succeeded_count == 0
    assert result.unavailable_count == len(execution_plan.tasks)
    assert result.failed_count == 0
    assert all(
        item.status == ExecutionStatus.UNAVAILABLE
        for item in result.task_results
    )


def test_runtime_rejects_duplicate_workflow_results():
    workflow = FinancialAnalysisWorkflow(_service())

    def duplicate_results(payload):
        result = workflow.run(FinancialQuery.model_validate(payload["query"]))
        result.calculations.append(result.calculations[0].model_copy())
        return result

    spec = FinancialToolSpec(
        tool_id=WORKFLOW_TOOL_ID,
        name="Duplicate Result Workflow",
        version="v10.2.1",
        input_schema="financial_workflow_runtime_input_v1",
        output_schema="financial_workflow_runtime_output_v1",
    )
    registry = FinancialToolExecutionRegistry([
        ExecutableFinancialTool(spec=spec, executor=duplicate_results),
    ])
    understanding = _understanding("分析贵州茅台 2022-2024 年收入")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_duplicate_results")

    result = FinancialRuntimeExecutor(
        workflow,
        tool_registry=registry,
    ).execute_plan(execution_plan, understanding=understanding)

    assert result.status == RuntimeExecutionStatus.FAILED
    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert result.metadata["error"].startswith("Duplicate calculation result for ")


def test_runtime_rejects_invalid_execution_plan():
    understanding = _understanding("分析贵州茅台 2022-2024 年收入")
    execution_plan = ExecutionPlanBuilder(
        semantic_resolver=FinancialSemanticResolver(_service())
    ).build(understanding, plan_id="plan_invalid")
    execution_plan.tasks[0].dependencies.append("exec_missing")

    with pytest.raises(RuntimeIntegrationError, match="Invalid execution plan"):
        FinancialRuntimeExecutor(
            FinancialAnalysisWorkflow(_service())
        ).execute_plan(execution_plan, understanding=understanding)

    assert execution_plan.status == ExecutionPlanStatus.FAILED
    assert execution_plan.finished_at is not None


def test_runtime_preserves_evidence_refs_after_registry_execution():
    calls = []
    workflow = FinancialAnalysisWorkflow(_service())
    understanding, _, execution_plan = _build_plan(
        "分析贵州茅台 2022-2024 年收入和净利润"
    )
    result = FinancialRuntimeExecutor(
        workflow,
        tool_registry=_registry_spy(workflow, calls),
    ).execute_plan(execution_plan, understanding=understanding)

    successful = [
        item for item in result.task_results
        if item.status == ExecutionStatus.SUCCEEDED
    ]
    assert successful
    assert len(calls) == 1

    result_by_task_id = {item.execution_task_id: item for item in result.task_results}
    for task in execution_plan.tasks:
        if task.status != ExecutionStatus.SUCCEEDED:
            continue
        task_result = result_by_task_id[task.task_id]
        assert task.output_refs == task_result.output_refs
        assert len(task.output_refs) == 3
        assert task_result.evidence_id in task.output_refs
        assert task_result.calculation_id in task.output_refs
        assert task_result.verification_id in task.output_refs
