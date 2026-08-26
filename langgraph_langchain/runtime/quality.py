"""Deterministic result-level quality checks for analysis runs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


class QualityIssue(BaseModel):
    severity: Literal["error", "warning", "disclosure"]
    code: str
    message: str
    finding_id: str | None = None


class AnalysisQualityResult(BaseModel):
    run_id: str
    passed: bool
    issues: list[QualityIssue] = Field(default_factory=list)
    checked_findings: int = 0
    checked_executions: int = 0


_PERCENT = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*%")
_TIME = re.compile(r"(20\d{2}|q[1-4]|month|week|day|quarter|year|期间|窗口|月份|季度|年度|日期)", re.I)
_CAUSAL = re.compile(r"(cause|caused|root cause|因果|导致|根因)", re.I)


def check_analysis_quality(snapshot: dict[str, Any], report: str = "") -> AnalysisQualityResult:
    run = snapshot.get("run") or {}
    findings = snapshot.get("findings") or []
    executions = snapshot.get("executions") or []
    artifacts = {str(item.get("artifact_id")) for item in snapshot.get("artifacts") or []}
    execution_ids = {str(item.get("execution_id")) for item in executions}
    issues: list[QualityIssue] = []

    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        evidence = finding.get("evidence") or []
        if not evidence:
            issues.append(QualityIssue(severity="error", code="finding_without_evidence", message="Finding has no bound evidence.", finding_id=finding_id))
            continue
        bound_execs = {
            str(identifier)
            for item in evidence
            for identifier in item.get("source_execution_ids") or []
        }
        bound_artifacts = {
            str(identifier)
            for item in evidence
            for identifier in item.get("source_artifact_ids") or []
        }
        bound_steps = {
            str(identifier)
            for item in evidence
            for identifier in item.get("source_step_ids") or []
        }
        step_ids = {str(item.get("step_id")) for item in (run.get("steps") or [])}
        if not bound_steps:
            issues.append(QualityIssue(severity="error", code="evidence_step_missing", message="Finding evidence must link to at least one runtime step.", finding_id=finding_id))
        elif bound_steps - step_ids:
            issues.append(QualityIssue(severity="error", code="evidence_step_unknown", message="Finding references a runtime step that is not present in the run.", finding_id=finding_id))
        if bound_execs - execution_ids:
            issues.append(QualityIssue(severity="error", code="evidence_execution_missing", message="Finding references an execution that is not present in the run.", finding_id=finding_id))
        if bound_artifacts - artifacts:
            issues.append(QualityIssue(severity="error", code="evidence_artifact_missing", message="Finding references an artifact that is not present in the run.", finding_id=finding_id))
        statement = str(finding.get("statement") or "")
        if _PERCENT.search(statement) and not any(
            (item.get("stats") or {}).get("denominator") is not None
            for item in evidence
            if isinstance(item, dict)
        ):
            issues.append(QualityIssue(severity="warning", code="percentage_without_denominator", message="Percentage claim should disclose its denominator.", finding_id=finding_id))
        if _TIME.search(statement) is None and any(token in statement.lower() for token in ("trend", "change", "增长", "下降", "趋势")):
            issues.append(QualityIssue(severity="warning", code="trend_without_time_window", message="Trend or change claim should include a time window.", finding_id=finding_id))
        if _CAUSAL.search(statement):
            level = str(finding.get("evidence_level") or "").upper()
            if level != "C":
                issues.append(QualityIssue(severity="warning", code="causal_language_evidence_mismatch", message="Causal language requires evidence level C or a downgraded statement.", finding_id=finding_id))

    if not executions and findings:
        issues.append(QualityIssue(severity="error", code="findings_without_executions", message="Findings exist but the run has no recorded executions."))
    if run.get("status") == "completed" and any(item.get("status") == "failed" for item in executions):
        issues.append(QualityIssue(severity="warning", code="completed_with_failed_execution", message="Completed run contains failed executions; disclose their scope."))
    if report and findings and "finding" not in report.lower() and "结论" not in report:
        issues.append(QualityIssue(severity="disclosure", code="report_finding_reference_missing", message="Report should reference the recorded findings."))

    return AnalysisQualityResult(
        run_id=str(run.get("run_id") or ""),
        passed=not any(item.severity == "error" for item in issues),
        issues=issues,
        checked_findings=len(findings),
        checked_executions=len(executions),
    )
