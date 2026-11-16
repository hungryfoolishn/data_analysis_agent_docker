"""Production runtime harness for Macro E2E (B9.8.2 stages 3-4).

Drives the REAL production chain end to end:

    MacroIntentResolver
      → MacroRuntimeIntegration.run()
      → GeneralAnalysisPlan (template / comparison builders)
      → general_plan_to_runtime_plan()
      → AnalysisRuntime.initialize_plan() (authoritative plan validation)
      → build_execution_controller() → RuntimeV2Controller.graph
      → Scheduler → Runner → TaskExecutor → StructuredTaskExecutor
      → real ``run_analysis_operation`` tool (SessionExecutionRecorder,
        canonical execution id, collect_operation_evidence,
        register_canonical_evidence)
      → record_execution_result (output binding adjudication, evidence
        content verification)
      → EvidenceRegistry → build_verified_report()

Only the LangGraph agent session shell is stubbed (a UI-side state holder
that is not part of the runtime evidence chain); every runtime-side module —
scheduler, runner, executor, tool, recorder, bindings, evidence registry,
report verification — is the production implementation.  External HTTP is
replaced by deterministic adapter transports (allowed per task §5.4/§6).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.evidence import EvidenceCollector
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.analysis_workflow import general_plan_to_runtime_plan
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.macro.intent import MacroRuntimeIntegration
from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
from langgraph_langchain.state_machine import AnalysisStateMachine
from langgraph_langchain.verification import verify_execution_result

CATALOG_PATH = (
    Path(__file__).parents[3] / "langgraph_langchain" / "runtime" / "macro" / "catalog.json"
)

_SKILLS_DIR = Path(__file__).parents[3] / "langgraph_langchain" / "skills"


def default_fred_transport(url: str, params: dict) -> dict:
    """Deterministic multi-year FRED payload: quarterly GDP + monthly CPI/PPI."""
    series = params.get("series_id") or url.rsplit("/", 1)[-1]
    if series == "GDP":
        rows: list[dict[str, str]] = []
        value = 20000.0
        for year in (2022, 2023, 2024, 2025):
            for quarter in range(1, 5):
                value *= 1.01
                rows.append({
                    "date": f"{year}-{quarter * 3 - 2:02d}-01",
                    "value": f"{value:.2f}",
                    "series_id": "GDP",
                })
        return {"observations": rows}
    value = 100.0
    rows = []
    for year in (2022, 2023, 2024, 2025):
        for month in range(1, 13):
            value *= 1.002
            rows.append({
                "date": f"{year}-{month:02d}-01",
                "value": f"{value:.3f}",
                "series_id": str(series),
            })
    return {"observations": rows}


def default_nbs_transport(url: str, params: dict) -> dict:
    """Deterministic multi-year NBS annual payload."""
    return {
        "returncode": 200,
        "ret": [{"datanodes": [
            {"data": {"code": "TEST", "sj": str(year), "data": 1349084 + idx * 25000}}
            for idx, year in enumerate(range(2021, 2026))
        ]}],
    }


class MacroProductionSession:
    """Agent-session shell for the production ``run_analysis_operation`` tool.

    Everything except ``analysis_runtime`` is UI-side session state that the
    production tool touches via ``_validate_tool_stage_factory`` and
    ``SessionExecutionRecorder``; none of it bypasses runtime evidence/binding
    modules.
    """

    def __init__(self, runtime: AnalysisRuntime) -> None:
        self.state_machine = AnalysisStateMachine()
        self.logger = logging.getLogger("macro_e2e")
        self.findings: list[dict] = []
        self.analysis_runtime = runtime
        self.current_runtime_step_id: Optional[str] = None
        self.new_artifacts: list[dict] = []
        self.current_asset_id: Optional[str] = None
        self.total_steps = 0

    @property
    def workspace_dir(self) -> Path:
        return self.analysis_runtime.workspace_dir


@dataclass
class MacroProductionRun:
    """Artifacts of one full production-chain macro execution."""

    runtime: AnalysisRuntime
    integration: MacroRuntimeIntegration
    session: MacroProductionSession
    outcome: dict[str, Any]
    controller: Any = None
    results: list[Any] = field(default_factory=list)

    @property
    def report(self):
        return self.runtime.build_verified_report()

    def verified_evidence(self):
        return self.runtime.evidence_registry.list(statuses={"verified"})


def build_macro_production_run(
    tmp_path: Path,
    question: str,
    *,
    name: str = "macro_prod",
    fred_transport: Optional[Callable[[str, dict], dict]] = None,
    nbs_transport: Optional[Callable[[str, dict], dict]] = None,
    runtime_kwargs: Optional[dict[str, Any]] = None,
) -> MacroProductionRun:
    """Plan and EXECUTE a macro question through the full production chain."""
    workspace = tmp_path / name
    runtime = AnalysisRuntime(
        workspace_dir=workspace,
        session_id=name,
        question=question,
        **(runtime_kwargs or {}),
    )
    session = MacroProductionSession(runtime)

    fred = FREDAdapter(cache=None)
    fred._transport = fred_transport or default_fred_transport
    fred._credentials = lambda: {"api_key": "test"}
    nbs = NBSAdapter(cache=None)
    nbs._transport = nbs_transport or default_nbs_transport
    registry = AdapterRegistry()
    registry.register(fred)
    registry.register(nbs)
    acquisition = AcquisitionService(
        runtime=runtime, registry=registry, history_dir=workspace / ".data_acq"
    )
    integration = MacroRuntimeIntegration(
        catalog=MacroIndicatorCatalog.load(CATALOG_PATH), acquisition=acquisition
    )

    outcome = integration.run(question)
    if outcome.get("status") != "planned":
        return MacroProductionRun(
            runtime=runtime, integration=integration, session=session, outcome=outcome
        )

    runtime_plan = general_plan_to_runtime_plan(outcome["plan"])
    # The production tool, built exactly like the agent builds session tools.
    from langgraph_langchain.tools.tool_analysis_operation import _factory

    tool = _factory(session)
    tool_map = {"run_analysis_operation": tool}
    runtime.initialize_plan(
        runtime_plan,
        max_running_tasks=1,
        execution_tools=tool_map,
    )

    # Production callbacks (langgraph_agent.py:1562-1566): on_task_start sets
    # the session step pointer; on_task_finish records the canonical result.
    def on_task_start(task) -> None:
        session.current_runtime_step_id = task.plan_step_id or task.task_id

    def on_task_finish(result) -> None:
        runtime.record_execution_result(result)
        session.total_steps += 1

    controller = runtime.build_execution_controller(
        structured_executor=StructuredTaskExecutor(
            tool_resolver=lambda tool_name: tool_map.get(tool_name)
        ),
        verifier=lambda result: verify_execution_result(
            result,
            artifacts=runtime.artifacts,
            workspace_dir=runtime.workspace_dir,
            context={
                "run_id": result.run_id,
                "task_id": result.task_id,
                "step_id": result.step_id,
            },
        ),
        evidence_factory=lambda result, verifications: EvidenceCollector().collect(
            execution=result,
            verification_results=verifications,
            artifacts=runtime.artifacts,
        ),
        on_task_start=on_task_start,
        on_task_finish=on_task_finish,
    )
    controller.graph.invoke({})
    results = list(runtime.executions)
    return MacroProductionRun(
        runtime=runtime,
        integration=integration,
        session=session,
        outcome=outcome,
        controller=controller,
        results=results,
    )


def execution_summary(run: MacroProductionRun) -> dict[str, Any]:
    """Compact lineage map of one executed run (task §10 §5 report shape)."""
    evidence = run.runtime.evidence_registry.list(statuses={"verified"})
    bindings = run.runtime.output_bindings.list_bindings() if hasattr(
        run.runtime.output_bindings, "list_bindings"
    ) else []
    first_evidence = evidence[0] if evidence else None
    return {
        "run_id": run.runtime.run.run_id,
        "attempt": run.runtime.run.attempt,
        "step_ids": [step.step_id for step in run.runtime.run.steps],
        "task_ids": [task.task_id for task in run.runtime.scheduler.tasks],
        "execution_ids": [item.execution_id for item in run.results],
        "output_asset_ids": sorted(
            {asset_id for item in run.results for asset_id in item.output_artifact_ids}
        ),
        "input_asset_ids": sorted(run.outcome["plan"].input_asset_ids),
        "evidence_ids": [item.evidence_id for item in evidence],
        "binding_count": len(bindings),
        "verified_report_id": (
            run.runtime.build_verified_report()[0].report_id
            if run.runtime.build_verified_report()[0] is not None
            else None
        ),
        "first_evidence": {
            "evidence_id": first_evidence.evidence_id,
            "task_id": first_evidence.task_id,
            "execution_id": first_evidence.execution_id,
            "input_asset_ids": first_evidence.input_asset_ids,
            "output_asset_ids": first_evidence.output_asset_ids,
        } if first_evidence else None,
    }
