"""Runtime V10.3.1 task-level financial execution tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.runtime.financial import (
    FinancialAnalysisWorkflow,
    FinancialDataService,
    FinancialQuery,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.models import Company, FinancialVerification
from langgraph_langchain.runtime.financial_agent import (
    DeterministicDAGScheduler,
    ExecutionPlan,
    ExecutionPlanBuilder,
    ExecutionPlanStatus,
    ExecutionStatus,
    ExecutionTask,
    FinancialRuntimeExecutor,
    FinancialSemanticResolver,
    FinancialTaskUnderstandingBuilder,
    RuntimeExecutionStatus,
    TaskExecutionOutcome,
    TaskLevelFinancialExecutor,
    TaskOutputStore,
)
from test_runtime_integration import (
    PERIODS,
    _assert_result_accounting,
    _balance,
    _build_plan,
    _cash,
    _company,
    _income,
    _service,
    _service_for_periods,
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

_AVERAGE_BALANCE_METRICS = {
    "roe",
    "roa",
    "receivable_turnover",
    "inventory_turnover",
    "asset_turnover",
}


def _metric_task(index: int, definition, *, dependencies=()):
    return ExecutionTask(
        task_id=f"exec_metric_{index:04d}",
        tool_id="financial_metric_engine",
        tool_version="v10.3.2",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id=definition.metric_id,
        metric_name=definition.name,
        dependencies=list(dependencies),
        required_data=list(definition.source_fields),
        inputs={"previous_period": "2022"},
        metadata={
            "metric_id": definition.metric_id,
            "previous_period": "2022",
        },
    )


def test_average_balance_golden_values_use_distinct_periods():
    workflow = FinancialAnalysisWorkflow(_service())
    expected = {
        "roe": {
            "numerator": 25.0,
            "current_denominator": 300.0,
            "previous_denominator": 200.0,
            "average_denominator": 250.0,
            "result": 10.0,
        },
        "roa": {
            "numerator": 25.0,
            "current_denominator": 500.0,
            "previous_denominator": 400.0,
            "average_denominator": 450.0,
            "result": 5.555556,
        },
        "receivable_turnover": {
            "numerator": 2123.0,
            "current_denominator": 30.0,
            "previous_denominator": 10.0,
            "average_denominator": 20.0,
            "result": 106.15,
        },
        "inventory_turnover": {
            "numerator": 30.0,
            "current_denominator": 40.0,
            "previous_denominator": 20.0,
            "average_denominator": 30.0,
            "result": 1.0,
        },
        "asset_turnover": {
            "numerator": 2123.0,
            "current_denominator": 500.0,
            "previous_denominator": 400.0,
            "average_denominator": 450.0,
            "result": 4.717778,
        },
    }

    for metric_id, expected_values in expected.items():
        definition = financial_metric_registry.get(metric_id)
        task = _metric_task(1, definition)
        outcome = TaskLevelFinancialExecutor(
            workflow,
            TaskOutputStore(),
        )(task)

        assert outcome.status == ExecutionStatus.SUCCEEDED
        calculation = outcome.output.calculation
        assert calculation.inputs == {
            key: expected_values[key]
            for key in (
                "numerator",
                "current_denominator",
                "previous_denominator",
                "average_denominator",
            )
        }
        assert calculation.result == pytest.approx(expected_values["result"])
        assert outcome.output.verification.passed is True
        assert outcome.output.verification.actual_value == pytest.approx(
            expected_values["result"]
        )


def test_core_financial_golden_values():
    service = _service()
    workflow = FinancialAnalysisWorkflow(service)
    expected = {
        "revenue_growth": 0.047125,
        "gross_margin": 3.297223,
        "net_margin": 1.177579,
        "free_cash_flow": 23.0,
    }
    for metric_id, expected_value in expected.items():
        definition = financial_metric_registry.get(metric_id)
        task = _metric_task(2, definition)
        outcome = TaskLevelFinancialExecutor(workflow, TaskOutputStore())(task)
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output.calculation.result == pytest.approx(expected_value)
        assert outcome.output.verification.passed is True

    negative_service = _service(income_overrides={
        "2022": {"revenue": 4000.0},
        "2023": {"revenue": 2000.0},
    })
    negative_workflow = FinancialAnalysisWorkflow(negative_service)
    definition = financial_metric_registry.get("revenue_growth")
    negative_outcome = TaskLevelFinancialExecutor(
        negative_workflow,
        TaskOutputStore(),
    )(_metric_task(3, definition))
    assert negative_outcome.status == ExecutionStatus.SUCCEEDED
    assert negative_outcome.output.calculation.result == pytest.approx(-50.0)


def test_average_balance_metric_without_previous_balance_is_unavailable():
    service = type(_service())(
        companies=[_company()],
        income_statements=[_income("2022"), _income("2023")],
        balance_sheets=[_balance("2023")],
        cash_flows=[_cash("2022"), _cash("2023")],
    )
    task = ExecutionTask(
        task_id="exec_roe_missing_previous_balance",
        tool_id="financial_metric_engine",
        tool_version="v10.3.2",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="roe",
        metric_name="净资产收益率",
    )
    executor = TaskLevelFinancialExecutor(
        FinancialAnalysisWorkflow(service),
        TaskOutputStore(),
    )
    outcome = executor(task)

    assert outcome.status == ExecutionStatus.UNAVAILABLE
    assert outcome.status_reason == "average_balance_previous_period_missing"
    assert outcome.output is None


def test_average_balance_denominator_zero_is_invalid():
    service = type(_service())(
        companies=[_company()],
        income_statements=[_income(period) for period in PERIODS],
        balance_sheets=[
            _balance("2022").model_copy(update={"total_equity": -50.0}),
            _balance("2023").model_copy(update={"total_equity": 50.0}),
        ],
        cash_flows=[_cash(period) for period in PERIODS],
    )
    definition = financial_metric_registry.get("roe")
    task = _metric_task(4, definition)
    outcome = TaskLevelFinancialExecutor(
        FinancialAnalysisWorkflow(service),
        TaskOutputStore(),
    )(task)

    assert outcome.status == ExecutionStatus.FAILED
    assert outcome.status_reason == "average_denominator_zero"
    assert outcome.output is None


def test_task_level_dag_matches_workflow_for_all_supported_metrics():
    definitions = financial_metric_registry.list_metrics()
    assert definitions
    task_id_by_metric = {
        definition.metric_id: f"exec_metric_{index:04d}"
        for index, definition in enumerate(definitions, start=1)
    }
    tasks = []
    for index, definition in enumerate(definitions, start=1):
        dependencies = []
        if definition.calculation_type == "growth":
            base_metric_id = definition.growth_of or ""
            if base_metric_id in task_id_by_metric:
                dependencies.append(task_id_by_metric[base_metric_id])
        tasks.append(_metric_task(
            index,
            definition,
            dependencies=dependencies,
        ))

    execution_plan = ExecutionPlan(
        plan_id="plan_all_metrics",
        query_id="query_all_metrics",
        tasks=tasks,
    )
    service = _service(income_overrides={
        period: {"eps": 1.0 + int(period)}
        for period in PERIODS
    })
    workflow = FinancialAnalysisWorkflow(service)
    understanding = _understanding("分析贵州茅台 2023年全部财务指标")
    dag_result = FinancialRuntimeExecutor(workflow).execute_plan(
        execution_plan,
        understanding=understanding,
    )

    assert dag_result.status == RuntimeExecutionStatus.SUCCEEDED
    assert dag_result.succeeded_count == len(definitions)
    assert dag_result.unavailable_count == 0
    assert dag_result.failed_count == 0
    assert dag_result.blocked_count == 0

    workflow_result = workflow.run(FinancialQuery(
        question=understanding.raw_question,
        task_type=understanding.task_type,
        company_names=["贵州茅台"],
        metrics=[definition.metric_id for definition in definitions],
        start_year=2023,
        end_year=2023,
    ))

    dag_calculations = _analysis_by_key(dag_result.analysis_result)
    workflow_calculations = _analysis_by_key(workflow_result)
    assert set(dag_calculations) == set(workflow_calculations)
    for key, calculation in dag_calculations.items():
        expected = workflow_calculations[key]
        assert calculation.result == expected.result
        assert calculation.formula == expected.formula
        assert calculation.status == expected.status
        assert calculation.inputs == expected.inputs
        assert calculation.missing_fields == expected.missing_fields
        assert calculation.unit == expected.unit

    dag_verifications = {
        (item.company_id, item.period, item.metric_id): item
        for item in dag_result.analysis_result.verifications
    }
    workflow_verifications = {
        (item.company_id, item.period, item.metric_id): item
        for item in workflow_result.verifications
    }
    assert set(dag_verifications) == set(workflow_verifications)
    for key, verification in dag_verifications.items():
        assert verification.passed is True
        assert verification.actual_value == pytest.approx(
            workflow_verifications[key].actual_value
        )

    expected_previous_denominator = {
        "roe": 200.0,
        "roa": 400.0,
        "receivable_turnover": 10.0,
        "inventory_turnover": 20.0,
        "asset_turnover": 400.0,
    }
    expected_average_denominator = {
        "roe": 250.0,
        "roa": 450.0,
        "receivable_turnover": 20.0,
        "inventory_turnover": 30.0,
        "asset_turnover": 450.0,
    }
    for metric_id, previous_denominator in expected_previous_denominator.items():
        calculation = dag_calculations[("600519", "2023", metric_id)]
        assert calculation.inputs["previous_denominator"] == previous_denominator
        assert calculation.inputs["average_denominator"] == (
            expected_average_denominator[metric_id]
        )


def test_growth_direct_read_propagates_base_computation_status():
    missing_previous = _service(income_overrides={
        "2022": {"net_profit": None},
    })
    missing_task = ExecutionTask(
        task_id="exec_growth_base_unavailable",
        tool_id="financial_metric_engine",
        tool_version="v10.3.2",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="net_profit_growth",
        metric_name="净利润增长率",
        inputs={"previous_period": "2022"},
        metadata={"previous_period": "2022"},
    )
    missing_outcome = TaskLevelFinancialExecutor(
        FinancialAnalysisWorkflow(missing_previous),
        TaskOutputStore(),
    )(missing_task)

    assert missing_outcome.status == ExecutionStatus.UNAVAILABLE
    assert missing_outcome.status_reason == (
        "previous_base_metric_unavailable:reported_value_missing"
    )

    invalid_previous = _service(income_overrides={
        "2022": {"revenue": float("nan")},
    })
    invalid_task = ExecutionTask(
        task_id="exec_growth_base_invalid",
        tool_id="financial_metric_engine",
        tool_version="v10.3.2",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        inputs={"previous_period": "2022"},
        metadata={"previous_period": "2022"},
    )
    invalid_outcome = TaskLevelFinancialExecutor(
        FinancialAnalysisWorkflow(invalid_previous),
        TaskOutputStore(),
    )(invalid_task)

    assert invalid_outcome.status == ExecutionStatus.FAILED
    assert invalid_outcome.status_reason == (
        "previous_base_metric_invalid:reported_value_not_finite"
    )


@pytest.mark.parametrize(
    ("income_overrides", "expected_status", "expected_reason"),
    [
        (
            {"2023": {"revenue": None}},
            ExecutionStatus.UNAVAILABLE,
            "current_base_metric_unavailable:reported_value_missing",
        ),
        (
            {"2023": {"revenue": float("nan")}},
            ExecutionStatus.FAILED,
            "current_base_metric_invalid:reported_value_not_finite",
        ),
        (
            {"2022": {"revenue": 0.0}},
            ExecutionStatus.FAILED,
            "growth_previous_period_zero",
        ),
    ],
)
def test_growth_direct_read_preserves_current_and_previous_edge_status(
    income_overrides,
    expected_status,
    expected_reason,
):
    workflow = FinancialAnalysisWorkflow(_service(income_overrides=income_overrides))
    task = ExecutionTask(
        task_id=f"exec_growth_edge_{expected_reason}",
        tool_id="financial_metric_engine",
        tool_version="v10.3.2",
        company_id="600519",
        company_name="贵州茅台",
        period="2023",
        metric_id="revenue_growth",
        metric_name="收入增长率",
        inputs={"previous_period": "2022"},
        metadata={"previous_period": "2022"},
    )
    outcome = TaskLevelFinancialExecutor(workflow, TaskOutputStore())(task)

    assert outcome.status == expected_status
    assert outcome.status_reason == expected_reason
    assert outcome.output is None


def test_quarter_and_half_year_periods_aggregate_without_error():
    periods = [
        "2022H2",
        "2022Q4",
        "2023Q1",
        "2023Q3",
        "2023Q4",
        "2023H1",
        "2023H2",
    ]
    service = _service_for_periods(periods, income_overrides={
        "2022H2": {"revenue": 180.0},
        "2022Q4": {"revenue": 90.0},
        "2023Q1": {"revenue": 100.0},
        "2023Q3": {"revenue": 105.0},
        "2023Q4": {"revenue": 110.0},
        "2023H1": {"revenue": 120.0},
        "2023H2": {"revenue": 130.0},
    })
    workflow = FinancialAnalysisWorkflow(service)
    understanding = _understanding("分析贵州茅台 2023年收入")

    def _period_plan(plan_id: str):
        return ExecutionPlan(
            plan_id=plan_id,
            query_id=f"query_{plan_id}",
            tasks=[
                ExecutionTask(
                    task_id=f"exec_period_{index:04d}",
                    tool_id="financial_metric_engine",
                    tool_version="v10.3.2",
                    company_id="600519",
                    company_name="贵州茅台",
                    period=period,
                    metric_id="revenue",
                    metric_name="营业收入",
                )
                for index, period in enumerate(
                    ["2023Q1", "2023Q4", "2023H1", "2023H2"],
                    start=1,
                )
            ],
        )

    dag_result = FinancialRuntimeExecutor(workflow).execute_plan(
        _period_plan("plan_period_dag"),
        understanding=understanding,
    )
    workflow_result = FinancialRuntimeExecutor(
        workflow,
        execution_mode="workflow",
    ).execute_plan(
        _period_plan("plan_period_workflow"),
        understanding=understanding,
    )

    for result in (dag_result, workflow_result):
        assert result.status == RuntimeExecutionStatus.SUCCEEDED
        assert result.succeeded_count == 4
        assert result.failed_count == 0
        assert result.blocked_count == 0
        assert result.analysis_result.query.start_year == 2023
        assert result.analysis_result.query.end_year == 2023
        observations = {
            item.period: item.value
            for item in result.analysis_result.observations
        }
        expected_observations = {
            "2023Q1": 100.0,
            "2023Q4": 110.0,
            "2023H1": 120.0,
            "2023H2": 130.0,
        }
        assert all(
            observations[period] == value
            for period, value in expected_observations.items()
        )
        if result is dag_result:
            assert observations == expected_observations
        else:
            assert "2023Q3" in observations
            assert observations["2023Q3"] == 105.0


def _peer_service():
    companies = [
        _company(),
        Company(
            company_id="000858",
            stock_code="000858",
            company_name="五粮液",
            exchange="SZSE",
            industry_id="baijiu",
        ),
    ]
    periods = ["2020", "2021", "2022", "2023", "2024"]
    revenues = {
        "600519": [100.0, 200.0, 100.0, 400.0, 4400.0],
        "000858": [100.0, 150.0, 180.0, 200.0, 220.0],
    }
    income_statements = []
    balance_sheets = []
    cash_flows = []
    for company in companies:
        for index, period in enumerate(periods):
            income_statements.append(_income(period).model_copy(update={
                "company_id": company.company_id,
                "source_id": f"income_{company.company_id}_{period}",
                "revenue": revenues[company.company_id][index],
            }))
            balance_sheets.append(_balance(period).model_copy(update={
                "company_id": company.company_id,
                "source_id": f"balance_{company.company_id}_{period}",
            }))
            cash_flows.append(_cash(period).model_copy(update={
                "company_id": company.company_id,
                "source_id": f"cash_{company.company_id}_{period}",
            }))
    return FinancialDataService(
        companies=companies,
        income_statements=income_statements,
        balance_sheets=balance_sheets,
        cash_flows=cash_flows,
    )


def _build_peer_plan(service, plan_id: str):
    understanding = FinancialTaskUnderstandingBuilder([
        "贵州茅台",
        "五粮液",
    ]).build("综合比较贵州茅台和五粮液 2020-2024年收入和净利润")
    resolver = FinancialSemanticResolver(service)
    plan = ExecutionPlanBuilder(
        semantic_resolver=resolver,
    ).build(understanding, plan_id=plan_id)
    return understanding, plan


def _assert_finding_evidence_chain(analysis):
    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    verification_by_id = {
        item.verification_id: item for item in analysis.verifications
    }
    calculation_by_id = {
        item.calculation_id: item for item in analysis.calculations
    }
    assert analysis.findings
    for finding in analysis.findings:
        assert finding.evidence_ids
        for evidence_id in finding.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            assert evidence.verification_status == "verified"
            assert evidence.calculation_id in calculation_by_id
            verification = verification_by_id[evidence.verification_result_id]
            assert verification.passed is True
            assert verification.calculation_id == evidence.calculation_id


def test_dag_mode_preserves_workflow_analysis_and_report_layers():
    service = _peer_service()
    understanding, dag_plan = _build_peer_plan(service, "plan_dag_analysis")
    _, workflow_plan = _build_peer_plan(service, "plan_workflow_analysis")

    dag_result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(service),
    ).execute_plan(dag_plan, understanding=understanding)
    workflow_result = FinancialRuntimeExecutor(
        FinancialAnalysisWorkflow(service),
        execution_mode="workflow",
    ).execute_plan(workflow_plan, understanding=understanding)

    assert dag_result.status == RuntimeExecutionStatus.PARTIAL
    assert dag_result.failed_count == 0
    assert dag_result.blocked_count == 0
    assert dag_result.unavailable_count > 0
    _assert_result_accounting(dag_result)
    _assert_result_accounting(workflow_result)

    dag_analysis = dag_result.analysis_result
    workflow_analysis = workflow_result.analysis_result
    assert dag_analysis.comparisons
    assert dag_analysis.risk_signals
    assert dag_analysis.anomalies
    assert dag_analysis.findings
    assert dag_result.metadata["comparison_count"] == len(
        dag_analysis.comparisons
    )
    assert dag_result.metadata["risk_signal_count"] == len(
        dag_analysis.risk_signals
    )
    assert dag_result.metadata["anomaly_count"] == len(dag_analysis.anomalies)
    assert dag_result.metadata["finding_count"] == len(dag_analysis.findings)

    assert sorted(
        (item.period, item.metric_id, item.leader_name, item.laggard_name)
        for item in dag_analysis.comparisons
    ) == sorted(
        (item.period, item.metric_id, item.leader_name, item.laggard_name)
        for item in workflow_analysis.comparisons
    )
    assert sorted(
        (item.company_name, item.period, item.metric_id, item.message)
        for item in dag_analysis.risk_signals
    ) == sorted(
        (item.company_name, item.period, item.metric_id, item.message)
        for item in workflow_analysis.risk_signals
    )
    assert sorted(
        (item.company_name, item.period, item.metric_id, item.message)
        for item in dag_analysis.anomalies
    ) == sorted(
        (item.company_name, item.period, item.metric_id, item.message)
        for item in workflow_analysis.anomalies
    )
    assert sorted(
        (item.finding_type, tuple(item.periods), item.metric_id, item.statement)
        for item in dag_analysis.findings
    ) == sorted(
        (item.finding_type, tuple(item.periods), item.metric_id, item.statement)
        for item in workflow_analysis.findings
    )
    _assert_finding_evidence_chain(dag_analysis)
    _assert_finding_evidence_chain(workflow_analysis)
    assert "## 同业比较" in dag_result.report_markdown
    assert "## 核心发现" in dag_result.report_markdown
    assert "## 风险信号" in dag_result.report_markdown
