"""Finding engine for Runtime V9 financial analysis."""

from __future__ import annotations

from .models import (
    FinancialComparison,
    FinancialEvidence,
    FinancialFinding,
    FinancialObservation,
    FinancialRiskSignal,
)


class FinancialFindingEngine:
    """Build evidence-backed findings from observations and comparisons."""

    def build(
        self,
        *,
        task_type: str,
        observations: list[FinancialObservation],
        evidence: list[FinancialEvidence],
        comparisons: list[FinancialComparison],
        risks: list[FinancialRiskSignal],
    ) -> list[FinancialFinding]:
        evidence_by_calculation = {
            item.calculation_id: item for item in evidence
        }
        findings: list[FinancialFinding] = []

        for observation in observations:
            evidence_item = evidence_by_calculation.get(observation.calculation.calculation_id)
            if evidence_item is None:
                continue
            finding_type = (
                "trend" if observation.metric_id.endswith("_growth") else "metric"
            )
            findings.append(FinancialFinding(
                finding_type=finding_type,
                statement=observation.fact,
                company_names=[observation.company_name],
                periods=[observation.period],
                metric_id=observation.metric_id,
                metric_name=observation.metric_name,
                evidence_ids=[evidence_item.evidence_id],
                calculation_ids=[observation.calculation.calculation_id],
                metadata={"source_ids": evidence_item.source_ids},
            ))

        for comparison in comparisons:
            evidence_ids = [
                item.evidence_id
                for item in evidence
                if item.period == comparison.period
                and item.metric_id == comparison.metric_id
                and item.company_name in {comparison.leader_name, comparison.laggard_name}
            ]
            findings.append(FinancialFinding(
                finding_type="peer_comparison",
                statement=comparison.statement,
                company_names=[comparison.leader_name, comparison.laggard_name],
                periods=[comparison.period],
                metric_id=comparison.metric_id,
                metric_name=comparison.metric_name,
                evidence_ids=evidence_ids,
                metadata={
                    "difference": comparison.difference,
                    "relative_difference": comparison.relative_difference,
                    "higher_is_better": comparison.higher_is_better,
                },
            ))

        for risk in risks:
            evidence_ids = [
                item.evidence_id
                for item in evidence
                if item.company_name == risk.company_name
                and item.period == risk.period
                and item.metric_id == risk.metric_id
            ]
            findings.append(FinancialFinding(
                finding_type="risk",
                statement=(
                    f"{risk.company_name} {risk.period} {risk.message}: "
                    f"{risk.display_value}。"
                ),
                company_names=[risk.company_name],
                periods=[risk.period],
                metric_id=risk.metric_id,
                evidence_ids=evidence_ids,
                risk_signal_ids=[risk.signal_id],
                metadata={"severity": risk.severity},
            ))

        if task_type == "ANOMALY_ANALYSIS":
            for observation in observations:
                if not observation.metric_id.endswith("_growth"):
                    continue
                if abs(float(observation.value)) < 10:
                    continue
                evidence_item = evidence_by_calculation.get(
                    observation.calculation.calculation_id
                )
                if evidence_item is None:
                    continue
                direction = "上升" if observation.value > 0 else "下降"
                findings.append(FinancialFinding(
                    finding_type="anomaly",
                    statement=(
                        f"{observation.company_name} {observation.period} "
                        f"{observation.metric_name}出现{direction} "
                        f"{abs(observation.value):.2f}%，超过 10% 异常阈值。"
                    ),
                    company_names=[observation.company_name],
                    periods=[observation.period],
                    metric_id=observation.metric_id,
                    metric_name=observation.metric_name,
                    evidence_ids=[evidence_item.evidence_id],
                    calculation_ids=[observation.calculation.calculation_id],
                    metadata={"threshold": 10.0},
                ))

        return findings
