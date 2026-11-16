"""B9.8.2 Stage 3: Macro evidence production-chain closure tests.

Every test executes through the REAL production chain (see harness.py):
real tool, real SessionExecutionRecorder, real canonical execution ids, real
output binding adjudication, real EvidenceRegistry, real report verification.
No _ScriptedTool, no manual current_runtime_step_id fixing, no mocked
binding/evidence modules (task §5.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.evidence import canonical_from_operation_evidence, collect_operation_evidence
from langgraph_langchain.evidence.registry import EvidenceRegistry, EvidenceRegistryError
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.analysis_workflow import general_plan_to_runtime_plan
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor

from tests.integration.macro.harness import build_macro_production_run

pytestmark = pytest.mark.macro_e2e


# ---------------------------------------------------------------------------
# §5.4-1/2/3: normal multi-step execution with evidence + verified report
# ---------------------------------------------------------------------------


def test_g15_full_production_execution(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_g15")
    assert run.outcome["status"] == "planned"
    tasks = run.runtime.scheduler.tasks
    assert tasks and all(task.status == "succeeded" for task in tasks)
    # one deduplicated execution result per planned task
    task_ids = [task.task_id for task in tasks]
    assert [result.task_id for result in run.results] == task_ids
    # every step recorded with canonical execution ids
    assert all(result.execution_id.startswith("exec_") for result in run.results)
    verified = run.verified_evidence()
    assert verified, "no evidence reached verified status"
    report, rendered = run.report
    assert report is not None and rendered
    assert report.status == "success"


def test_g16_blocked_comparison_still_produces_evidence_and_report(tmp_path):
    run = build_macro_production_run(tmp_path, "中美GDP增长对比", name="ev_g16")
    assert run.outcome["status"] == "planned"
    assert all(task.status == "succeeded" for task in run.runtime.scheduler.tasks)
    verified = run.verified_evidence()
    assert verified
    report, _ = run.report
    assert report is not None
    # both countries' source assets traceable through report findings
    country_assets = set(run.outcome["plan"].input_asset_ids)
    referenced = {
        asset_id
        for finding in report.findings
        for asset_id in finding.referenced_asset_ids
    }
    assert country_assets <= referenced


# ---------------------------------------------------------------------------
# §5.3: evidence ↔ run/step/task/execution/binding/asset linkage
# ---------------------------------------------------------------------------


def test_evidence_run_step_task_execution_linkage(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_link")
    executions_by_task = {result.task_id: result for result in run.results}
    verified = run.verified_evidence()
    assert verified
    for evidence in verified:
        execution = executions_by_task[evidence.task_id]
        assert evidence.execution_id == execution.execution_id
        assert evidence.tool_name == execution.tool_name == "run_analysis_operation"
        # task belongs to the run's step with the same plan_step_id
        task = run.runtime.scheduler.get_task(evidence.task_id)
        assert task.plan_step_id in {step.step_id for step in run.runtime.run.steps}
    # bindings registered for every output under the CURRENT attempt
    bindings = run.runtime.output_bindings.list_all()
    assert bindings
    task_ids = {task.task_id for task in run.runtime.scheduler.tasks}
    for binding in bindings:
        assert binding.run_id == run.runtime.run.run_id
        assert binding.execution_id in {r.execution_id for r in run.results}


def test_evidence_references_input_and_output_assets(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_assets")
    verified = run.verified_evidence()
    assert verified
    registered_assets = set(run.runtime.assets)
    for evidence in verified:
        assert evidence.input_asset_ids, "evidence must reference its inputs"
        assert len(evidence.output_asset_ids) == 1
        assert evidence.input_asset_ids[0] in registered_assets
        assert evidence.output_asset_ids[0] in registered_assets
    # dq legs consume the two distinct country source assets — no overwrite
    dq_task_ids = [
        task.task_id
        for task in run.runtime.scheduler.tasks
        if task.task_id.startswith("task_")
    ]
    inputs = {
        tuple(item.input_asset_ids)
        for item in verified
    }
    distinct_inputs = {pair[0] for pair in inputs}
    if len(dq_task_ids) >= 2 and len(verified) >= 2:
        assert len(distinct_inputs) >= 2


def test_retry_generates_new_attempt_identity(tmp_path):
    """Retry = new run attempt that replans and re-executes; fresh canonical
    execution identity; bindings belong to the current attempt (v3 §8,
    B11.2.1 semantics)."""
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_retry")
    parent_run_id = run.runtime.run.run_id
    parent_execution_ids = {result.execution_id for result in run.results}
    first_step_id = run.runtime.run.steps[0].step_id

    retry_run = build_macro_production_run(
        tmp_path,
        "对比中美CPI同比变化",
        name="ev_retry",
        runtime_kwargs={
            "force_new_run": True,
            "parent_run_id": parent_run_id,
            "retry_of_step_id": first_step_id,
        },
    )
    assert retry_run.runtime.run.attempt == 2
    assert retry_run.runtime.run.parent_run_id == parent_run_id

    retry_executions = retry_run.runtime.executions
    assert retry_executions
    assert {item.execution_id for item in retry_executions}.isdisjoint(parent_execution_ids)
    assert all(task.status == "succeeded" for task in retry_run.runtime.scheduler.tasks)
    # retry bindings are written under the retry run (attempt 2)
    bindings = retry_run.runtime.output_bindings.list_all()
    assert bindings
    assert all(binding.run_id == retry_run.runtime.run.run_id for binding in bindings)


# ---------------------------------------------------------------------------
# §5.4-9: wrong task_id evidence is rejected
# ---------------------------------------------------------------------------


def test_wrong_task_id_evidence_rejected(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_wrong_task")
    verified = run.verified_evidence()
    assert verified
    template = verified[0]
    execution = next(
        item for item in run.results if item.execution_id == template.execution_id
    )
    forged = template.model_copy(deep=True)
    forged.task_id = "task_does_not_exist"
    with pytest.raises(EvidenceRegistryError, match="task_id"):
        run.runtime.evidence_registry.register(forged, replace=True)


def test_unknown_execution_evidence_rejected(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_wrong_exec")
    verified = run.verified_evidence()
    template = verified[0]
    forged = template.model_copy(deep=True)
    forged.evidence_id = "evid_forged_0001"
    forged.execution_id = "exec_unknown_0001"
    with pytest.raises(EvidenceRegistryError):
        run.runtime.evidence_registry.register(forged)


# ---------------------------------------------------------------------------
# §5.4-10: missing binding is rejected at the runtime plan boundary
# ---------------------------------------------------------------------------


def test_missing_binding_reference_rejected_at_plan_validation(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_no_bind")
    plan = general_plan_to_runtime_plan(run.outcome["plan"])
    # Point the last step at a logical output no step declares.
    plan.steps[-1].required_inputs = ["step_missing0000.output"]
    plan.steps[-1].parameters["asset_id"] = "step_missing0000.output"
    from langgraph_langchain.tools.tool_analysis_operation import _factory

    tool = _factory(run.session)
    tool_map = {"run_analysis_operation": tool}
    fresh_runtime = AnalysisRuntime(
        workspace_dir=run.runtime.workspace_dir.parent / "ev_no_bind_fresh",
        session_id="ev_no_bind_fresh",
        question="对比中美CPI同比变化",
    )
    with pytest.raises(Exception, match="step_missing0000"):
        fresh_runtime.initialize_plan(plan, max_running_tasks=1, execution_tools=tool_map)


# ---------------------------------------------------------------------------
# §5.4-11: missing evidence blocks the verified report
# ---------------------------------------------------------------------------


def test_missing_verified_evidence_yields_no_report(tmp_path):
    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_no_evid")
    assert run.verified_evidence()
    # Swap in an empty registry: a verified report is impossible without
    # evidence (public construction, no registry internals touched).
    from langgraph_langchain.evidence.registry import EvidenceRegistry

    run.runtime.evidence_registry = EvidenceRegistry(
        assets=run.runtime.assets,
        artifacts=run.runtime.artifacts,
        executions=run.runtime._lookup_execution,
    )
    report, rendered = run.runtime.build_verified_report()
    assert report is None and rendered is None


def test_report_with_unknown_evidence_reference_rejected(tmp_path):
    from langgraph_langchain.runtime.analysis_report import (
        ReportFinding,
        build_analysis_report,
        validate_analysis_report,
    )

    run = build_macro_production_run(tmp_path, "对比中美CPI同比变化", name="ev_bad_ref")
    evidence_records = run.runtime.evidence_registry.report_records()
    bogus_finding = ReportFinding(
        finding_id="F001",
        claim="cross-country comparison result",
        supported_by=["evid_unknown_0001"],
        referenced_asset_ids=sorted(run.runtime.assets),
        calculation_ref="run_analysis_operation:exec_unknown",
    )
    report = build_analysis_report(
        task_id=run.runtime.task.task_id,
        user_goal="对比中美CPI同比变化",
        status="success",
        executive_summary="test",
        findings=[bogus_finding],
        artifact_ids=[],
        limitations=[],
    )
    validation = validate_analysis_report(
        report,
        evidence_records=evidence_records,
        asset_ids=set(run.runtime.assets),
        artifact_ids=set(run.runtime.artifacts),
    )
    assert not validation.is_valid
    assert any("evid_unknown_0001" in error for error in validation.errors)
