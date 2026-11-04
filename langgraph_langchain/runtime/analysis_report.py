"""Structured, evidence-backed analysis reports and validators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?")


class ReportValidationError(ValueError):
    """Raised when a report cannot be accepted due to lineage failures."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _numbers(value: Any) -> list[str]:
    return [item.replace(",", "") for item in _NUMBER_RE.findall(str(value or ""))]


@dataclass
class ReportValidationResult:
    status: Literal["valid", "warning", "invalid"] = "valid"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.status != "invalid"


class ReportFinding(BaseModel):
    finding_id: str
    claim: str
    supported_by: list[str] = Field(min_length=1)
    referenced_asset_ids: list[str] = Field(default_factory=list)
    calculation_ref: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    limitation_notes: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    title: str
    body: str
    finding_ids: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    report_id: str = Field(default_factory=lambda: f"report_{uuid4().hex}")
    task_id: str
    user_goal: str
    status: Literal["success", "partial_success", "failed"] = "success"
    executive_summary: str
    sections: list[ReportSection] = Field(default_factory=list)
    findings: list[ReportFinding] = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=_utc_now)

    @property
    def evidence_ids(self) -> list[str]:
        return list(dict.fromkeys(
            evidence_id
            for finding in self.findings
            for evidence_id in finding.supported_by
        ))


def validate_analysis_report(
    report: AnalysisReport,
    *,
    evidence_records: Mapping[str, Mapping[str, Any]],
    asset_ids: Iterable[str],
    artifact_ids: Iterable[str] = (),
) -> ReportValidationResult:
    """Return structured errors/warnings for report lineage and consistency."""
    result = ReportValidationResult()
    assets = set(asset_ids)
    artifacts = set(artifact_ids)
    report_evidence = set(report.evidence_ids)

    for evidence_id in report_evidence:
        if evidence_id not in evidence_records:
            result.errors.append(f"Unknown evidence reference: {evidence_id}")

    for finding in report.findings:
        if not finding.supported_by:
            result.errors.append(
                f"Finding {finding.finding_id} has no supporting evidence"
            )
        for evidence_id in finding.supported_by:
            evidence = evidence_records.get(evidence_id)
            if evidence is None:
                result.errors.append(
                    f"Finding {finding.finding_id} uses unknown evidence {evidence_id}"
                )
                continue
            if str(evidence.get("validation_status", "")) != "verified":
                result.errors.append(
                    f"Finding {finding.finding_id} uses unverified evidence {evidence_id}"
                )

        for asset_id in finding.referenced_asset_ids:
            if asset_id not in assets:
                result.errors.append(
                    f"Finding {finding.finding_id} references unknown asset {asset_id}"
                )

        claim_numbers = set(_numbers(finding.claim))
        supported_numbers: set[str] = set()
        for evidence_id in finding.supported_by:
            evidence = evidence_records.get(evidence_id) or {}
            numeric_values = evidence.get("numeric_values") or {}
            if isinstance(numeric_values, Mapping):
                supported_numbers.update(_numbers(numeric_values))
            else:
                supported_numbers.update(_numbers(numeric_values))
            supported_numbers.update(_numbers(evidence.get("evidence_text")))
        unsupported = claim_numbers - supported_numbers
        if unsupported:
            result.errors.append(
                f"Finding {finding.finding_id} contains untraceable numbers: "
                + ", ".join(sorted(unsupported))
            )

        uses_truncated = any(
            bool((evidence_records.get(evidence_id) or {}).get("truncated"))
            for evidence_id in finding.supported_by
        )
        limitation_text = " ".join(
            [
                *finding.limitation_notes,
                *report.limitations,
            ]
        ).lower()
        if uses_truncated and "truncat" not in limitation_text:
            result.warnings.append(
                f"Finding {finding.finding_id} uses truncated data without a limitation"
            )

    for artifact_id in report.artifact_ids:
        if artifact_id not in artifacts:
            result.errors.append(f"Unknown artifact reference: {artifact_id}")

    if result.errors:
        result.status = "invalid"
    elif result.warnings:
        result.status = "warning"
    else:
        result.status = "valid"
    return result


def build_analysis_report(
    *,
    task_id: str,
    user_goal: str,
    status: Literal["success", "partial_success", "failed"],
    executive_summary: str,
    findings: Sequence[ReportFinding],
    sections: Sequence[ReportSection] = (),
    artifact_ids: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> AnalysisReport:
    """Deterministically assemble a report; narrative generation remains separate."""
    report = AnalysisReport(
        task_id=task_id,
        user_goal=user_goal,
        status=status,
        executive_summary=executive_summary,
        sections=list(sections),
        findings=list(findings),
        artifact_ids=list(artifact_ids),
        limitations=list(limitations),
    )
    evidence_ids = {
        evidence_id
        for finding in report.findings
        for evidence_id in finding.supported_by
    }
    if not evidence_ids:
        raise ReportValidationError("A successful report requires evidence")
    return report


def render_analysis_report(report: AnalysisReport) -> str:
    """Render a deterministic markdown view without adding new claims."""
    lines = [
        f"# Analysis Report: {report.user_goal}",
        "",
        f"- Report ID: `{report.report_id}`",
        f"- Task ID: `{report.task_id}`",
        f"- Status: `{report.status}`",
        "",
        "## Executive Summary",
        "",
        report.executive_summary,
        "",
    ]
    if report.limitations:
        lines.extend(["## Limitations", ""])
        lines.extend(f"- {item}" for item in report.limitations)
        lines.append("")

    lines.extend(["## Findings", ""])
    for finding in report.findings:
        lines.append(
            f"- **{finding.finding_id}** ({finding.confidence}): {finding.claim}"
        )
        lines.append(
            "  - Evidence: " + ", ".join(f"`{item}`" for item in finding.supported_by)
        )
        if finding.referenced_asset_ids:
            lines.append(
                "  - Assets: "
                + ", ".join(f"`{item}`" for item in finding.referenced_asset_ids)
            )
        if finding.limitation_notes:
            lines.append(
                "  - Limitations: " + "; ".join(finding.limitation_notes)
            )

    if report.sections:
        lines.extend(["", "## Analysis", ""])
        for section in report.sections:
            lines.extend([f"### {section.title}", "", section.body, ""])

    if report.artifact_ids:
        lines.extend(["", "## Artifacts", ""])
        lines.extend(f"- `{item}`" for item in report.artifact_ids)

    lines.extend(["", "## Provenance", ""])
    lines.append(f"- Generated at: {report.generated_at}")
    lines.append("- All findings require verified evidence references.")
    return "\n".join(lines)
