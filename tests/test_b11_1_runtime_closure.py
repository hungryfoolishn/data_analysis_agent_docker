from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.evidence import (
    CanonicalEvidence,
    EvidenceRegistry,
    EvidenceRegistryError,
    canonical_from_operation_evidence,
    collect_operation_evidence,
)
from langgraph_langchain.runtime.analysis_operations import execute_analysis_operation
from langgraph_langchain.runtime.analysis_workflow import prepare_file_analysis
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.outcome import RuntimeOutcome
from langgraph_langchain.runtime.plans import (
    PlanExecutionValidationError,
    validate_runtime_plan,
)
from langgraph_langchain.runtime.runtime_route import (
    RuntimeSelectionPolicy,
    decide_runtime_route,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


def _runtime(tmp_path: Path) -> AnalysisRuntime:
    return AnalysisRuntime(
        workspace_dir=tmp_path,
        session_id="b11-integration",
        question="Run deterministic analysis",
    )


def _file_plan(tmp_path: Path, runtime: AnalysisRuntime):
    frame = pd.DataFrame(
        {
            "region": ["North", "North", "South"],
            "amount": [100.0, 120.0, 80.0],
        }
    )
    path = tmp_path / "sales.csv"
    frame.to_csv(path, index=False)
    return prepare_file_analysis(runtime, path, goal="Sum total amount group by region")


def _plan_step(**kwargs) -> RuntimePlanStep:
    return RuntimePlanStep(**kwargs)


def test_runtime_selection_policy_branches():
    disabled = RuntimeSelectionPolicy(enabled=False)
    enabled = RuntimeSelectionPolicy(enabled=True)

    assert decide_runtime_route(policy=disabled).selected_runtime == "legacy_v1"
    assert decide_runtime_route(policy=enabled).selected_runtime == "runtime_v2"

    business = decide_runtime_route(
        policy=enabled,
        v2_action="failed",
        v2_has_result=True,
        error_type="ToolExecutionError",
    )
    assert business.selected_runtime == "runtime_v2"
    assert business.fallback is False

    strict_infra = decide_runtime_route(
        policy=RuntimeSelectionPolicy(enabled=True, fallback_enabled=True, strict_mode=True),
        v2_action="blocked",
        v2_has_result=False,
        error_type="RuntimeInfrastructureError",
    )
    assert strict_infra.fallback is False

    allowed_infra = decide_runtime_route(
        policy=RuntimeSelectionPolicy(enabled=True, fallback_enabled=True, strict_mode=False),
        v2_action="blocked",
        v2_has_result=False,
        error_type="RuntimeInfrastructureError",
    )
    assert allowed_infra.selected_runtime == "legacy_v1"
    assert allowed_infra.fallback is True


def test_plan_execution_validation_rejects_invalid_tools_and_sql():
    plan = AnalysisPlan(
        goal="bad plan",
        input_asset_ids=["asset_1"],
        steps=[
            _plan_step(
                step_id="step_1",
                objective="unsupported",
                method="arbitrary_python",
                required_inputs=["asset_1"],
                expected_outputs=["output_1"],
            )
        ],
    )
    errors = validate_runtime_plan(
        plan,
        available_methods={"run_analysis_operation"},
        available_asset_ids={"asset_1"},
    )
    assert any("Unknown analysis method" in error for error in errors)


def test_plan_execution_validation_rejects_unknown_asset_and_cycle():
    plan = AnalysisPlan(
        goal="cycle",
        input_asset_ids=["asset_missing"],
        steps=[
            _plan_step(
                step_id="step_1",
                objective="one",
                method="run_analysis_operation",
                required_inputs=["asset_missing"],
                expected_outputs=["output_1"],
                parameters={"asset_id": "asset_missing", "operation": "inspect"},
            ),
            _plan_step(
                step_id="step_2",
                objective="two",
                method="run_analysis_operation",
                required_inputs=["asset_missing"],
                expected_outputs=["output_2"],
                depends_on=["step_1"],
                parameters={"asset_id": "asset_missing", "operation": "sort"},
            ),
        ],
    )
    plan.steps[0].depends_on = ["step_2"]
    errors = validate_runtime_plan(
        plan,
        available_methods={"run_analysis_operation"},
        available_asset_ids=set(),
    )
    assert any("Unknown input asset" in error for error in errors)
    assert any("dependency cycle" in error for error in errors)


def test_plan_execution_validation_rejects_write_sql():
    plan = AnalysisPlan(
        goal="unsafe sql",
        input_asset_ids=["asset_db"],
        steps=[
            _plan_step(
                step_id="sql_step",
                objective="delete",
                method="run_sqlite_query",
                required_inputs=["asset_db"],
                expected_outputs=["query_result"],
                parameters={"sql": "DELETE FROM orders"},
            )
        ],
    )
    errors = validate_runtime_plan(
        plan,
        available_methods={"run_sqlite_query"},
        available_asset_ids={"asset_db"},
    )
    assert any("not a read-only SQLite query" in error for error in errors)


def test_file_analysis_plan_is_execution_valid(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared = _file_plan(tmp_path, runtime)
    errors = validate_runtime_plan(
        prepared.runtime_plan,
        available_methods={"run_analysis_operation"},
        available_asset_ids=set(runtime.assets),
    )
    assert errors == []


def test_canonical_evidence_lifecycle(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared = _file_plan(tmp_path, runtime)
    resolved = runtime.asset_resolver.resolve(prepared.asset.asset_id)
    output = execute_analysis_operation(
        resolved.dataframe,
        operation="group_aggregate",
        parameters={"group_by": "region", "metric": "amount", "agg": "sum"},
    )
    output_path = tmp_path / "output.parquet"
    output.to_parquet(output_path, index=False)
    output_asset = runtime.register_dataframe(
        dataframe=output,
        source_path=output_path,
        source_type="parquet",
        source_metadata={"operation": "group_aggregate"},
    )
    artifact = runtime.register_artifact(output_path, created_by_tool="run_analysis_operation")
    operation_evidence = collect_operation_evidence(
        execution_id="exec_evidence_1",
        operation="group_aggregate",
        parameters={"group_by": "region", "metric": "amount", "agg": "sum"},
        input_asset_ids=[prepared.asset.asset_id],
        output_asset=output_asset,
        numeric_values={"amount_sum": 300.0},
    )
    execution = runtime.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_evidence_1",
        step_id="step_1",
    )
    canonical = canonical_from_operation_evidence(
        operation_evidence,
        task_id="step_1",
        artifact_ids=[artifact.artifact_id],
    )
    assert canonical.validation_status == "unverified"
    registered = runtime.register_canonical_evidence(canonical)
    assert registered.validation_status == "reference_validated"
    assert runtime.evidence_registry.get(registered.evidence_id) is registered

    verified = runtime.verify_canonical_evidence(registered.evidence_id)
    assert verified.validation_status == "verified"

    with pytest.raises(EvidenceRegistryError):
        runtime.register_canonical_evidence(canonical, artifact_ids=[])


def test_canonical_evidence_rejects_unknown_references(tmp_path: Path):
    evidence = CanonicalEvidence(
        evidence_type="analysis_operation",
        execution_id="exec_1",
        tool_name="run_analysis_operation",
        input_asset_ids=["asset_missing"],
        evidence_text="bad references",
    )
    registry = EvidenceRegistry()
    with pytest.raises(EvidenceRegistryError, match="Unknown asset IDs"):
        registry.register(evidence)


def test_report_closure_success_and_partial(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared = _file_plan(tmp_path, runtime)
    resolved = runtime.asset_resolver.resolve(prepared.asset.asset_id)
    output = execute_analysis_operation(
        resolved.dataframe,
        operation="group_aggregate",
        parameters={"group_by": "region", "metric": "amount", "agg": "sum"},
    )
    output_path = tmp_path / "output.parquet"
    output.to_parquet(output_path, index=False)
    output_asset = runtime.register_dataframe(
        dataframe=output,
        source_path=output_path,
        source_type="parquet",
        source_metadata={"operation": "group_aggregate"},
    )
    artifact = runtime.register_artifact(output_path, created_by_tool="run_analysis_operation")
    evidence = collect_operation_evidence(
        execution_id="exec_report_1",
        operation="group_aggregate",
        parameters={"group_by": "region", "metric": "amount", "agg": "sum"},
        input_asset_ids=[prepared.asset.asset_id],
        output_asset=output_asset,
        numeric_values={"amount_sum": 300.0},
    )
    execution = runtime.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_report_1",
        step_id=runtime.scheduler.tasks[0].task_id,
    )
    canonical = canonical_from_operation_evidence(
        evidence,
        task_id="step_1",
        artifact_ids=[artifact.artifact_id],
    )
    runtime.register_canonical_evidence(canonical)
    runtime.verify_canonical_evidence(canonical.evidence_id)

    report, markdown = runtime.build_verified_report()
    assert report is not None
    assert report.status == "success"
    assert report.evidence_ids == [canonical.evidence_id]
    assert "F001" in markdown
    assert "deterministic execution results" in markdown

    runtime.scheduler.tasks[0].status = "failed"
    runtime.scheduler.tasks[1].status = "succeeded"
    second_evidence = collect_operation_evidence(
        execution_id="exec_report_2",
        operation="inspect",
        parameters={"rows": 5},
        input_asset_ids=[prepared.asset.asset_id],
        output_asset=output_asset,
        numeric_values={"row_count": 3.0},
    )
    runtime.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_report_2",
        step_id=runtime.scheduler.tasks[1].task_id,
    )
    second_canonical = canonical_from_operation_evidence(
        second_evidence,
        task_id=runtime.scheduler.tasks[1].task_id,
        artifact_ids=[artifact.artifact_id],
    )
    runtime.register_canonical_evidence(second_canonical)
    report, markdown = runtime.build_verified_report()
    assert report is not None
    assert report.status == "partial_success"
    outcome = runtime.build_runtime_outcome(runtime_mode="runtime_v2", report=report)
    assert isinstance(outcome, RuntimeOutcome)
    assert outcome.status == "partial_success"
    assert outcome.completed_steps == 1
    assert outcome.failed_steps == 1
    assert canonical.evidence_id in outcome.evidence_ids


def test_report_closure_requires_verified_evidence(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared = _file_plan(tmp_path, runtime)
    evidence = CanonicalEvidence(
        evidence_type="analysis_operation",
        execution_id="exec_unverified",
        task_id="step_1",
        tool_name="run_analysis_operation",
        input_asset_ids=[prepared.asset.asset_id],
        evidence_text="unverified operation",
    )
    runtime.record_execution(
        tool_name="run_analysis_operation",
        status="succeeded",
        execution_id="exec_unverified",
        step_id="step_1",
    )
    runtime.evidence_registry.register(evidence)
    report, markdown = runtime.build_verified_report()
    assert report is None
    assert markdown is None
    outcome = runtime.build_runtime_outcome(runtime_mode="runtime_v2", report=None)
    assert outcome.status == "failed"
    assert outcome.error_code == "report_not_generated"
import asyncio
from pathlib import Path

import pandas as pd

from langgraph_langchain.langgraph_agent import (
    _Session,
    _run_runtime_v2_graph_stream,
)


def test_runtime_v2_file_execution_creates_report_and_outcome(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "region": ["North", "North", "South"],
            "amount": [100.0, 120.0, 80.0],
        }
    )
    source = tmp_path / "sales.csv"
    frame.to_csv(source, index=False)
    session = _Session(
        str(tmp_path / "workspace"),
        str(source),
        session_id="b11-runtime-e2e",
        user_question="Sum total amount group by region",
    )
    tools = session.analysis_runtime.asset_resolver and __import__(
        "langgraph_langchain.langgraph_agent", fromlist=["_make_tools"]
    )._make_tools(session)

    async def collect():
        return [chunk async for chunk, _ in _run_runtime_v2_graph_stream(session, tools=tools, source_path=str(source))]

    chunks = asyncio.run(collect())
    assert any("Runtime V2 structured plan completed" in chunk for chunk in chunks)
    runtime = session.analysis_runtime
    assert session.report is not None
    assert session.runtime_outcome.status == "completed"
    assert session.runtime_outcome.runtime_mode == "runtime_v2"
    assert len(runtime.evidence_registry.list(statuses={"verified"})) >= 1

def test_evidence_registry_rejects_fake_execution():
    registry = EvidenceRegistry(
        assets={"asset_1": object()},
        artifacts={"artifact_1": object()},
        executions={"exec_real": object()},
    )
    evidence = CanonicalEvidence(
        evidence_type="analysis_operation",
        execution_id="exec_fake",
        tool_name="run_analysis_operation",
        input_asset_ids=["asset_1"],
        artifact_ids=["artifact_1"],
        evidence_text="fabricated execution",
    )
    with pytest.raises(EvidenceRegistryError, match="Unknown execution_id"):
        registry.register(evidence)


def test_legacy_evidence_does_not_inherit_verified_status():
    from langgraph_langchain.schemas import EvidenceItem
    from langgraph_langchain.evidence import canonical_from_legacy_evidence_item

    evidence = EvidenceItem(
        evidence_id="evid_legacy",
        evidence_text="legacy verified evidence",
        verification_status="verified",
        verification_result_id="verify_1",
        source_execution_ids=["exec_legacy"],
        source_asset_ids=["asset_legacy"],
        source_artifact_ids=["artifact_legacy"],
    )
    canonical = canonical_from_legacy_evidence_item(evidence)
    assert canonical.validation_status == "unverified"
    assert canonical.source_metadata["legacy_validation_status"] == "verified"


def test_evidence_without_content_validator_cannot_be_verified():
    registry = EvidenceRegistry(
        assets={"asset_1": object()},
        artifacts={"artifact_1": object()},
        executions={"exec_1": object()},
    )
    evidence = CanonicalEvidence(
        evidence_type="analysis_operation",
        execution_id="exec_1",
        tool_name="run_analysis_operation",
        input_asset_ids=["asset_1"],
        artifact_ids=["artifact_1"],
        evidence_text="references are valid",
    )
    registry.register(evidence)
    with pytest.raises(
        EvidenceRegistryError,
        match="content validator is required",
    ):
        registry.verify(evidence.evidence_id)


def test_runtime_outcome_maps_skipped_and_cancelled(tmp_path: Path):
    runtime = _runtime(tmp_path)
    prepared = _file_plan(tmp_path, runtime)
    runtime.scheduler.tasks[0].status = "succeeded"
    runtime.scheduler.tasks[1].status = "skipped"

    outcome = runtime.build_runtime_outcome(runtime_mode="runtime_v2")
    assert outcome.status == "partial_success"

    runtime.scheduler.tasks[1].status = "cancelled"
    outcome = runtime.build_runtime_outcome(runtime_mode="runtime_v2")
    assert outcome.status == "cancelled"
