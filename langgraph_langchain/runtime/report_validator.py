"""Report integrity validation for Runtime V7.

The report layer may summarize verified findings, but may not introduce numbers
that cannot be traced back to verified evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from langgraph_langchain.schemas import EvidenceItem, Finding


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?%?")


@dataclass
class ReportValidationResult:
    errors: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.unsupported_numbers


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff%.,-]+", "", text)
    return text


def _normalise_number(value: str) -> str:
    normalized = value.strip().replace(",", "")
    return normalized.rstrip("%")


def _numbers(value: Any) -> list[str]:
    return [_normalise_number(item) for item in _NUMBER_RE.findall(str(value or ""))]


def _numeric_proofs(values: set[str]) -> set[str]:
    """Accept a signed metric and its narrative magnitude as equivalent.

    A finding may write "-10500" in structured stats while the report says
    "declined by 10500".  The magnitude remains traceable; the direction is
    expressed by the finding/report wording and is still tied to one number.
    """
    proofs: set[str] = set(values)
    for value in values:
        if value.startswith("-"):
            proofs.add(value[1:])
    return proofs


def _evidence_numbers(evidence: EvidenceItem) -> set[str]:
    values: set[str] = set(_numbers(evidence.evidence_text))
    stats = evidence.stats or {}
    for value in stats.values():
        values.update(_numbers(value))
    return values


def _finding_tokens(finding: Finding) -> set[str]:
    return {token for token in re.findall(r"[\w\u4e00-\u9fff]+", finding.statement.lower()) if len(token) > 1}


def _report_contains_finding(report: str, finding: Finding) -> bool:
    report_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", report.lower()))
    finding_tokens = _finding_tokens(finding)
    if not finding_tokens:
        return True
    overlap = len(report_tokens & finding_tokens) / len(finding_tokens)
    return overlap >= 0.65


def validate_report(
    report: str,
    findings: Sequence[Finding],
    evidence: Optional[Iterable[EvidenceItem]] = None,
) -> ReportValidationResult:
    """Validate that a report only reports findings backed by verified evidence."""
    result = ReportValidationResult()
    report_text = str(report or "")

    findings = [
        item if isinstance(item, Finding) else Finding.model_validate(item)
        for item in findings or []
    ]
    if not findings:
        result.errors.append("report has no findings")
        return result

    evidence_items = list(evidence or [])
    inferred = {item.evidence_id: item for item in evidence_items}
    for finding in findings:
        for item in finding.evidence:
            inferred.setdefault(item.evidence_id, item)

    evidence_by_id = {
        str(_value(item, "evidence_id")): item for item in inferred.values()
    }

    report_numbers = set(_numbers(report_text))
    supported_numbers: set[str] = set()
    for item in evidence_by_id.values():
        if _value(item, "verification_status") == "verified":
            supported_numbers.update(_numeric_proofs(_evidence_numbers(item)))

    for finding in findings:
        if not finding.supported_by:
            result.errors.append(f"finding {finding.finding_id} has no supported_by evidence")
            continue
        if not finding.evidence:
            result.errors.append(f"finding {finding.finding_id} has no embedded evidence")
            continue

        for evidence_id in finding.supported_by:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                result.errors.append(
                    f"finding {finding.finding_id} references unknown evidence {evidence_id}"
                )
                continue
            if _value(item, "verification_status") != "verified":
                result.errors.append(
                    f"finding {finding.finding_id} uses unverified evidence {evidence_id}"
                )
                continue
            if not _value(item, "verification_result_id"):
                result.errors.append(
                    f"evidence {evidence_id} has no verification_result_id"
                )
            supported_numbers.update(_numeric_proofs(_evidence_numbers(item)))

        if not _report_contains_finding(report_text, finding):
            result.errors.append(
                f"report does not contain finding {finding.finding_id}"
            )

    unsupported = sorted(
        report_numbers - supported_numbers,
        key=lambda value: (len(value), value),
    )
    result.unsupported_numbers = unsupported
    if unsupported:
        result.errors.append(
            "report contains numbers not present in verified evidence: "
            + ", ".join(unsupported)
        )
    return result
