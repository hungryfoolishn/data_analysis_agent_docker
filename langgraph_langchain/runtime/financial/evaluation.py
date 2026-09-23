"""Financial evaluation adapter for Runtime V9 golden regression."""

from __future__ import annotations

import hashlib
from typing import Iterable

from langgraph_langchain.runtime.evaluation import (
    GoldenCandidateResult,
    GoldenCase,
    GoldenEvaluationRunner,
    MetricAnswer,
)
from langgraph_langchain.runtime.evaluation.models import EvaluationRun
from langgraph_langchain.runtime.financial.metrics import financial_metric_registry
from langgraph_langchain.runtime.financial.workflow import FinancialAnalysisWorkflow


class FinancialEvaluationAdapter:
    """Convert verified financial workflow output into golden candidates."""

    SKILL_ID = "runtime_financial_workflow"
    SKILL_NAME = "financial-analysis-workflow"
    SKILL_VERSION = "9.2.0"

    def __init__(self, workflow: FinancialAnalysisWorkflow) -> None:
        self.workflow = workflow
        self.skill_hash = self._compute_skill_hash()

    @staticmethod
    def _compute_skill_hash() -> str:
        digest = hashlib.sha256()
        for definition in financial_metric_registry.list_metrics():
            digest.update(definition.metric_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(definition.formula.encode("utf-8"))
            digest.update(b"\n")
            digest.update(definition.balance_policy.encode("utf-8"))
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
        metric_answers.extend(
            MetricAnswer(
                metric=f"peer_difference:{item.metric_id}",
                value=float(item.difference),
                period=item.period,
                dimension="peer",
                group=f"{item.leader_name}|{item.laggard_name}",
                filters={
                    "leader": item.leader_name,
                    "laggard": item.laggard_name,
                },
            )
            for item in result.comparisons
        )
        metric_answers.extend(
            MetricAnswer(
                metric=f"peer_relative_difference:{item.metric_id}",
                value=float(item.relative_difference),
                period=item.period,
                dimension="peer",
                group=f"{item.leader_name}|{item.laggard_name}",
                filters={
                    "leader": item.leader_name,
                    "laggard": item.laggard_name,
                },
            )
            for item in result.comparisons
        )

        verified_evidence_count = sum(
            item.verification_status == "verified" for item in result.evidence
        )
        succeeded = (
            bool(result.observations)
            and bool(result.evidence)
            and bool(result.findings)
            and verified_evidence_count == len(result.evidence)
        )
        return GoldenCandidateResult(
            case_id=case.case_id,
            run_id="financial_golden_v9_2",
            status="succeeded" if succeeded else "failed",
            verification_passed=succeeded,
            metric_answers=metric_answers,
            evidence_count=len(result.evidence),
            verified_evidence_count=verified_evidence_count,
            finding_count=len(result.findings),
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
                "observation_count": len(result.observations),
                "calculation_count": len(result.calculations),
                "verification_count": len(result.verifications),
                "unavailable_calculation_count": sum(
                    item.status == "unavailable" for item in result.calculations
                ),
                "invalid_calculation_count": sum(
                    item.status == "invalid" for item in result.calculations
                ),
                "evidence_count": len(result.evidence),
                "data_source_count": len(result.data_sources),
                "verified_evidence_count": verified_evidence_count,
                "comparison_count": len(result.comparisons),
                "finding_count": len(result.findings),
                "risk_signal_count": len(result.risk_signals),
            },
        )

    def run_dataset(
        self,
        cases: Iterable[GoldenCase],
        *,
        run_id: str = "financial_golden_v9_2",
    ) -> EvaluationRun:
        items = list(cases)
        candidates = {case.case_id: self.run_case(case) for case in items}
        return GoldenEvaluationRunner().run_dataset(items, candidates, run_id=run_id)
