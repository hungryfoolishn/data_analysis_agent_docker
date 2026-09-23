"""Finding engine for Runtime V9 financial analysis."""

from __future__ import annotations

from .models import (
    FinancialAnomalySignal,
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
        anomalies: list[FinancialAnomalySignal],
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

        for anomaly in anomalies:
            evidence_ids = [
                item.evidence_id
                for item in evidence
                if item.company_name == anomaly.company_name
                and item.period == anomaly.period
                and item.metric_id == anomaly.metric_id
            ]
            findings.append(FinancialFinding(
                finding_type="anomaly",
                statement=anomaly.message,
                company_names=[anomaly.company_name],
                periods=[anomaly.period],
                metric_id=anomaly.metric_id,
                metric_name=anomaly.metric_name,
                evidence_ids=evidence_ids,
                anomaly_ids=[anomaly.anomaly_id],
                metadata={
                    "historical_mean": anomaly.historical_mean,
                    "historical_std": anomaly.historical_std,
                    "z_score": anomaly.z_score,
                    "threshold": anomaly.threshold,
                    "method": anomaly.method,
                },
            ))

        return findings
