"""Financial evaluation adapter for Runtime V9 golden regression."""

from __future__ import annotations

from typing import Iterable

from langgraph_langchain.runtime.evaluation import (
    GoldenCandidateResult,
    GoldenCase,
    GoldenEvaluationRunner,
    MetricAnswer,
)
from langgraph_langchain.runtime.evaluation.models import EvaluationRun

from .metrics import financial_metric_registry
from .workflow import FinancialAnalysisWorkflow


class FinancialEvaluationAdapter:
    """Convert deterministic financial workflow output to golden candidates."""

    SKILL_ID = "runtime_financial_workflow"
    SKILL_NAME = "financial-analysis-workflow"
    SKILL_VERSION = "9.0.0"

    def __init__(self, workflow: FinancialAnalysisWorkflow) -> None:
        self.workflow = workflow
        self.skill_hash = self._compute_skill_hash()

    @staticmethod
    def _compute_skill_hash() -> str:
        import hashlib

        digest = hashlib.sha256()
        for definition in financial_metric_registry.list_metrics():
            digest.update(definition.metric_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(definition.formula.encode("utf-8"))
            digest.update(b"\n")
        return f"sha256:{digest.hexdigest()}"

    def run_case(self, case: GoldenCase) -> GoldenCandidateResult:
        result = self.workflow.run(case.question)
        metric_answers = [
            MetricAnswer(
                metric=item.metric_id,
                value=float(item.value),
                period=item.period,
                dimension="company",
                group=item.company_name,
                filters={"company": item.company_name},
            )
            for item in result.observations
        ]
        calculation_count = len(result.calculations)
        observation_count = len(result.observations)
        succeeded = observation_count > 0 and calculation_count >= observation_count
        return GoldenCandidateResult(
            case_id=case.case_id,
            run_id="financial_golden_v9",
            status="succeeded" if succeeded else "failed",
            verification_passed=succeeded,
            metric_answers=metric_answers,
            evidence_count=calculation_count,
            verified_evidence_count=calculation_count if succeeded else 0,
            finding_count=1 if result.observations else 0,
            sql_text="",
            report_text=result.report_markdown,
            duration_ms=0.0,
            skill_id=self.SKILL_ID,
            skill_name=self.SKILL_NAME,
            skill_version=self.SKILL_VERSION,
            skill_hash=self.skill_hash,
            metadata={
                "task_type": result.task_type,
                "company_count": len({
                    item.company_name for item in result.observations
                }),
                "observation_count": observation_count,
                "calculation_count": calculation_count,
                "risk_signal_count": len(result.risk_signals),
            },
        )

    def run_dataset(
        self,
        cases: Iterable[GoldenCase],
        *,
        run_id: str = "financial_golden_v9",
    ) -> EvaluationRun:
        items = list(cases)
        candidates = {case.case_id: self.run_case(case) for case in items}
        return GoldenEvaluationRunner().run_dataset(items, candidates, run_id=run_id)
