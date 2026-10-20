"""Runtime V10.3.1 task-level financial execution tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial import FinancialAnalysisWorkflow
from langgraph_langchain.runtime.financial.models import FinancialVerification
from langgraph_langchain.runtime.financial_agent import (
    DeterministicDAGScheduler,
    ExecutionPlan,
    ExecutionPlanBuilder,
    ExecutionPlanStatus,
    ExecutionStatus,
    ExecutionTask,
    FinancialRuntimeExecutor,
    FinancialSemanticResolver,
    RuntimeExecutionStatus,
    TaskExecutionOutcome,
    TaskLevelFinancialExecutor,
    TaskOutputStore,
)
from test_runtime_integration import (
    _build_plan,
    _cash,
    _company,
    _income,
    _service,
    _understanding,
)


def _build_fresh_plan(question: str):
    understanding = _understanding(question)
    resolver = FinancialSemanticResolver(_service())
    plan = ExecutionPlanBuilder(semantic_resolver=resolver).build(
        understanding,
        plan_id="plan_task_level_test",
    )
    return understanding, resolver, plan


def _result_by_key(result):
    return {
        (item.company_id, item.period, item.metric_id): item
        for item in result.task_results
    }


def _analysis_by_key(analysis_result):
    return {
        (item.company_id, item.period, item.metric_id): item
        for item in analysis_result.calculations
    }


def test_task_level_dag_executor_consumes_dependency_outputs_and_matches_workflow():
    understanding, _, dag_plan = _build_fresh_plan(
        "分析贵州茅台 2022-2024 年收入和收入增长率"
    )
    legacy_understanding, _, workflow_plan = _build_fresh_plan(
        "分析贵州茅台 2022-2024 年收入和收入增长率"
    )
    workflow = FinancialAnalysisWorkflow(_service())

    dag_result = FinancialRuntimeExecutor(workflow).execute_plan(
        dag_plan,
        understanding=understanding,
    )
    workflow_result = FinancialRuntimeExecutor(
        workflow,
        execution_mode="workflow",
    ).execute_plan(
        workflow_plan,
        understanding=legacy_understanding,
    )

    assert dag_result.status == RuntimeExecutionStatus.SUCCEEDED
    assert dag_plan.status == ExecutionPlanStatus.SUCCEEDED
    assert dag_result.succeeded_count == 6
    assert dag_result.unavailable_count == 0
    assert dag_result.failed_count == 0
    assert dag_result.blocked_count == 0
    assert dag_result.metadata["execution_mode"] == "dag"
    assert dag_result.metadata["dependency_output_count"] == 3
    assert dag_result.report_markdown

    dag_by_key = _result_by_key(dag_result)
    growth = dag_by_key[("600519", "2023", "revenue_growth")]
    current_revenue = dag_by_key[("600519", "2023", "revenue")]
    previous_revenue = dag_by_key[("600519", "2022", "revenue")]
    assert growth.metadata["input_sources"] == {
        "current": f"dependency_task:{current_revenue.execution_task_id}",
        "previous": f"dependency_task:{previous_revenue.execution_task_id}",
    }
    assert growth.calculation_id
    assert growth.verification_id
    assert growth.evidence_id
    assert len(growth.output_refs) == 3

    dag_analysis = _analysis_by_key(dag_result.analysis_result)
    workflow_analysis = _analysis_by_key(workflow_result.analysis_result)
    assert set(dag_analysis) == set(workflow_analysis)
    for key, dag_calculation in dag_analysis.items():
        workflow_calculation = workflow_analysis[key]
        assert dag_calculation.result == workflow_calculation.result
        assert dag_calculation.formula == workflow_calculation.formula
        assert dag_calculation.status == workflow_calculation.status

    dag_verifications = {
        (item.company_id, item.period, item.metric_id): item
        for item in dag_result.analysis_result.verifications
    }
    workflow_verifications = {
        (item.company_id, item.period, item.metric_id): item
        for item in workflow_result.analysis_result.verifications
    }
    assert set(dag_verifications) == set(workflow_verifications)
    for key, verification in dag_verifications.items():
        assert verification.passed is True
        assert verification.actual_value == workflow_verifications[key].actual_value

    dag_evidence = {
        (item.company_id, item.period, item.metric_id): item
        for item in dag_result.analysis_result.evidence
    }
    assert set(dag_evidence) == set(dag_analysis)
    assert all(item.value is not None for item in dag_evidence.values())


def test_growth_task_reads_direct_data_when_dependency_tasks_are_absent():
    workflow = FinancialAnalysisWorkflow(_service())
    output_store = TaskOutputStore()
    task = ExecutionTask(
        task_id="exec_growth_direct",
        tool_id="financial_metric_engine",
        tool_version="v10.3.1",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        status=ExecutionStatus.PENDING,
        metadata={"metric_id": "revenue_growth", "previous_period": "2022"},
    )
    execution_plan = ExecutionPlan(
        plan_id="plan_growth_direct",
        query_id="query_growth_direct",
        tasks=[task],
    )

    scheduler = DeterministicDAGScheduler(
        TaskLevelFinancialExecutor(workflow, output_store),
        on_outcome=output_store.record,
    )
    result = scheduler.run(execution_plan)

    assert result.status == RuntimeExecutionStatus.SUCCEEDED
    assert result.succeeded_count == 1
    assert result.task_results[0].metadata["input_sources"] == {
        "current": "data_service",
        "previous": "data_service",
    }
    assert output_store.has(task.task_id)
    assert output_store.get(task.task_id).value == pytest.approx(0.047125)


def test_missing_previous_period_is_unavailable_not_failed():
    service = type(_service())(
        companies=[_company()],
        income_statements=[_income("2023")],
        balance_sheets=[],
        cash_flows=[],
    )
    workflow = FinancialAnalysisWorkflow(service)
    output_store = TaskOutputStore()
    task = ExecutionTask(
        task_id="exec_growth_missing_previous",
        tool_id="financial_metric_engine",
        tool_version="v10.3.1",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        status=ExecutionStatus.PENDING,
        metadata={"metric_id": "revenue_growth", "previous_period": "2022"},
    )
    executor = TaskLevelFinancialExecutor(workflow, output_store)
    outcome = executor(task)

    assert outcome.status == ExecutionStatus.UNAVAILABLE
    assert outcome.status_reason == (
        "missing_financial_data: 'No financial statements for 600519 2023'"
    )
    assert outcome.output is None


def test_upstream_verification_failure_blocks_dependent_growth_task(monkeypatch):
    understanding, _, plan = _build_fresh_plan(
        "分析贵州茅台 2022-2024 年收入和收入增长率"
    )
    workflow = FinancialAnalysisWorkflow(_service())
    original_verify = workflow.verification_engine.verify

    def verify(*, calculation, **kwargs):
        if (
            calculation.metric_id == "revenue"
            and calculation.period == "2023"
        ):
            return FinancialVerification(
                metric_id=calculation.metric_id,
                company_id=calculation.company_id,
                company_name=calculation.company_name,
                period=calculation.period,
                status="failed",
                passed=False,
                expected_value=calculation.result,
                actual_value=calculation.result,
                message="forced verification failure",
                calculation_id=calculation.calculation_id,
            )
        return original_verify(calculation=calculation, **kwargs)

    monkeypatch.setattr(workflow.verification_engine, "verify", verify)
    result = FinancialRuntimeExecutor(workflow).execute_plan(
        plan,
        understanding=understanding,
    )

    assert result.status == RuntimeExecutionStatus.FAILED
    assert plan.status == ExecutionPlanStatus.FAILED
    assert result.succeeded_count == 3
    assert result.failed_count == 1
    assert result.blocked_count == 2
    assert result.unavailable_count == 0
    assert plan.finished_at is not None

    failed = next(
        item for item in result.task_results
        if item.period == "2023" and item.metric_id == "revenue"
    )
    blocked = next(
        item for item in result.task_results
        if item.period == "2023" and item.metric_id == "revenue_growth"
    )
    assert failed.status == ExecutionStatus.FAILED
    assert failed.status_reason == "forced verification failure"
    assert blocked.status == ExecutionStatus.BLOCKED
    assert blocked.status_reason == "dependency exec_0003 failed"
    assert blocked.calculation_id is None
    assert blocked.verification_id is None
    assert blocked.evidence_id is None
    assert "exec_0004" not in result.metadata["execution_order"]
    assert "exec_0006" not in result.metadata["execution_order"]


def test_output_store_rejects_successful_outcome_without_typed_output():
    store = TaskOutputStore()
    task = ExecutionTask(
        task_id="exec_bad_output",
        tool_id="financial_metric_engine",
        tool_version="v10.3.1",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue",
        metric_name="营业收入",
    )
    outcome = TaskExecutionOutcome(status=ExecutionStatus.SUCCEEDED)

    with pytest.raises(TypeError, match="TaskExecutionOutput"):
        store.record(task, outcome)


def test_unknown_metric_is_execution_failure_not_data_unavailable():
    workflow = FinancialAnalysisWorkflow(_service())
    task = ExecutionTask(
        task_id="exec_unknown_metric",
        tool_id="financial_metric_engine",
        tool_version="v10.3.1",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="unsupported_metric",
        metric_name="Unsupported Metric",
    )
    executor = TaskLevelFinancialExecutor(workflow, TaskOutputStore())

    outcome = executor(task)

    assert outcome.status == ExecutionStatus.FAILED
    assert outcome.status_reason == "unsupported financial metric: unsupported_metric"
    assert outcome.output is None
