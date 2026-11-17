"""Canonical Financial Domain Runtime integration (B10.6).

The only execution path is Runtime V2.  This module builds an explicit plan and
uses a structured tool; it does not replace the scheduler, runner, Evidence
registry, or SnapshotStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.graph import RuntimeV2Controller, build_runtime_v2_controller
from langgraph_langchain.runtime.plans import AnalysisPlan

from .catalog import CALCULATED, FinancialIndicatorResult
from .context import FinancialContext
from .evidence import register_financial_evidence
from .intent import FinancialIntent
from .templates import FinancialTemplateRegistry, financial_template_registry

__all__ = ["FinancialDomainRuntime", "FinancialIndicatorTool", "FinancialRuntimeModeError"]


class FinancialRuntimeModeError(ValueError):
    """Raised when an execution mode and Evidence wiring are incompatible."""


class FinancialIndicatorTool:
    def __init__(self, context: FinancialContext) -> None:
        self.context = context

    def invoke(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        result = self.context.resolve(str(arguments.get("metric_id") or self.context.intent.metric_id))
        if result.status != CALCULATED:
            return {
                "status": result.status,
                "metric_id": result.metric_id,
                "reason": result.reason,
                "inputs": dict(result.inputs),
            }
        return {
            "status": "ok",
            "metric_id": result.metric_id,
            "value": str(result.value),
            "statistic_kind": result.statistic_kind,
            "unit": result.unit,
            "inputs": dict(result.inputs),
            "lineage": dict(result.lineage),
        }


@dataclass(frozen=True)
class FinancialDomainRuntimeResult:
    controller: RuntimeV2Controller
    execution: Any
    indicator: FinancialIndicatorResult
    evidence: Any | None = None


class FinancialDomainRuntime:
    def __init__(
        self,
        *,
        context: FinancialContext,
        templates: FinancialTemplateRegistry | None = None,
        evidence_registry: Any | None = None,
        mode: str = "offline_calculation",
    ) -> None:
        if mode not in {"offline_calculation", "platform_verified_execution"}:
            raise FinancialRuntimeModeError(f"unsupported Financial runtime mode: {mode!r}")
        self.context = context
        self.templates = templates or financial_template_registry
        self.evidence_registry = evidence_registry
        self.mode = mode

    def run(
        self,
        intent: FinancialIntent,
        *,
        template_id: str = "single_metric_analysis",
        session_id: str = "financial_domain",
        run_id: str = "financial_domain_run",
        task_id: str | None = None,
    ) -> FinancialDomainRuntimeResult:
        template = self.templates.get(template_id)
        plan: AnalysisPlan = template.build_plan(intent)
        tool = FinancialIndicatorTool(self.context)
        controller: RuntimeV2Controller = build_runtime_v2_controller(
            plan=plan,
            session_id=session_id,
            run_id=run_id,
            structured_executor=StructuredTaskExecutor(tool_resolver=lambda name: tool),
        )
        state = controller.graph.invoke({})
        execution = state.get("result")
        if execution is not None and task_id:
            execution = execution.model_copy(update={"task_id": task_id})
        indicator = self.context.resolve(intent.metric_id)
        evidence = None
        if self.mode == "platform_verified_execution":
            if self.evidence_registry is None:
                raise FinancialRuntimeModeError(
                    "platform_verified_execution requires EvidenceRegistry"
                )
            if not task_id:
                raise FinancialRuntimeModeError(
                    "platform_verified_execution requires explicit task_id"
                )
            if not self.context.input_asset_ids:
                raise FinancialRuntimeModeError(
                    "platform_verified_execution requires DataAsset input lineage"
                )
            if execution is not None and hasattr(self.evidence_registry, "register_execution"):
                self.evidence_registry.register_execution(execution)
        if self.mode == "platform_verified_execution" and execution is not None:
            if indicator.status == CALCULATED:
                evidence = register_financial_evidence(
                    self.evidence_registry,
                    execution=execution,
                    indicator=indicator,
                    evidence_text=(
                        f"Computed canonical Financial indicator {indicator.metric_id} "
                        f"with explicit {indicator.statistic_kind} semantics."
                    ),
                    input_asset_ids=self.context.input_asset_ids,
                    source_metadata={
                        "template_id": template_id,
                        "intent": intent.to_parameters(),
                    },
                )
        return FinancialDomainRuntimeResult(
            controller=controller,
            execution=execution,
            indicator=indicator,
            evidence=evidence,
        )
