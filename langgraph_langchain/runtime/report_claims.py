"""Claim-level provenance for generated analysis reports."""

from __future__ import annotations

import hashlib
import re
from numbers import Real
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.models import utc_now


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", str(text or "").casefold())
        if len(token) > 1
    }



_NUMBER_RE = re.compile(
    r"(?<![\w])(?P<value>-?\d+(?:\.\d+)?)"
    r"(?:\s*(?P<scale>万|亿))?(?P<percent>%)?"
)
_DATE_RE = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?|\d{4}\s*年(?:\s*\d{1,2}\s*月)?)\b|\bQ[1-4]\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]{1,}\b")

_CONCEPT_PATTERNS = {
    "department": ("department", "dept", "部门"),
    "revenue": ("revenue", "sales", "销售额", "销售收入", "收入"),
    "orders": ("orders", "order", "订单"),
    "decline": ("decline", "decrease", "drop", "下滑", "下降", "减少"),
    "increase": ("increase", "growth", "增长", "上升", "提升"),
    "contribution": ("contribution", "贡献", "贡献来源"),
    "attainment": ("attainment", "达标", "达标率"),
    "anomaly": ("anomaly", "outlier", "异常"),
    "trend": ("trend", "趋势"),
    "quality": ("quality", "质量"),
    "period": ("period", "month", "quarter", "月", "季度"),
}


def _numeric_values(value: Any) -> set[float]:
    """Extract scale- and format-independent numeric facts from text or stats."""
    values: set[float] = set()

    def _walk(item: Any) -> None:
        if isinstance(item, Real) and not isinstance(item, bool):
            values.add(float(item))
        elif isinstance(item, str):
            for match in _NUMBER_RE.finditer(item):
                number = float(match.group("value"))
                scale = match.group("scale")
                if scale == "万":
                    number *= 10_000
                elif scale == "亿":
                    number *= 100_000_000
                values.add(number)
                if match.group("percent"):
                    values.add(number / 100.0)
        elif isinstance(item, Mapping):
            for child in item.values():
                _walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                _walk(child)

    _walk(value)
    return values


def _concept_tokens(text: Any) -> set[str]:
    value = str(text or "").casefold()
    return {
        concept
        for concept, patterns in _CONCEPT_PATTERNS.items()
        if any(pattern in value for pattern in patterns)
    }


def _entity_tokens(text: Any) -> set[str]:
    return {
        item.casefold()
        for item in _ENTITY_RE.findall(str(text or ""))
    }


def _time_tokens(text: Any) -> set[str]:
    return {
        item.casefold()
        for item in _DATE_RE.findall(str(text or ""))
    }

def _claim_candidates(report_text: str) -> list[str]:
    claims: list[str] = []
    for raw_line in str(report_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", line)
        if not match:
            continue
        text = match.group(1).strip()
        if len(text) < 8:
            continue
        claims.append(text)
    return claims


class ReportClaim(BaseModel):
    claim_id: str
    text: str
    finding_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimLineage(BaseModel):
    claim_id: str
    finding_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    complete: bool = False
    missing: list[str] = Field(default_factory=list)


def extract_report_claims(
    report_text: str,
    findings: Iterable[Any],
    evidence: Iterable[Any] | None = None,
) -> list[ReportClaim]:
    """Split report bullets into claims and map them to Runtime findings."""
    finding_items = list(findings or [])
    finding_evidence_by_id = {
        str(_value(item, "finding_id")): list(_value(item, "evidence") or [])
        for item in finding_items
        if _value(item, "finding_id") is not None
    }
    evidence_items = {
        str(_value(item, "evidence_id")): item
        for item in (evidence or [])
        if _value(item, "evidence_id") is not None
    }
    claims: list[ReportClaim] = []
    seen_text: set[str] = set()

    for text in _claim_candidates(report_text):
        normalized = re.sub(r"\s+", " ", text)
        if normalized.casefold() in seen_text:
            continue
        seen_text.add(normalized.casefold())

        claim_tokens = _tokens(normalized)
        claim_numbers = _numeric_values(normalized)
        claim_concepts = _concept_tokens(normalized)
        claim_entities = _entity_tokens(normalized)
        claim_times = _time_tokens(normalized)

        matched_findings: list[tuple[float, float, float, Any]] = []
        for finding in finding_items:
            statement_tokens = _tokens(_value(finding, "statement"))
            if not statement_tokens:
                continue
            overlap = len(claim_tokens & statement_tokens) / len(statement_tokens)

            finding_evidence = list(_value(finding, "evidence") or [])
            evidence_text_tokens: set[str] = set()
            finding_numbers: set[float] = set(_numeric_values(_value(finding, "statement")))
            finding_concepts = set(_concept_tokens(_value(finding, "statement")))
            finding_concepts.update(
                _concept_tokens(" ".join(_value(finding, "source_fields") or []))
            )
            finding_entities = set(_entity_tokens(_value(finding, "statement")))
            finding_times = set(_time_tokens(_value(finding, "statement")))

            for item in finding_evidence:
                evidence_text = _value(item, "evidence_text")
                evidence_text_tokens.update(_tokens(evidence_text))
                finding_numbers.update(_numeric_values(evidence_text))
                finding_numbers.update(_numeric_values(_value(item, "stats")))
                finding_concepts.update(_concept_tokens(evidence_text))
                finding_concepts.update(
                    _concept_tokens(" ".join(_value(item, "source_fields") or []))
                )
                finding_entities.update(_entity_tokens(evidence_text))
                finding_times.update(_time_tokens(evidence_text))

            evidence_overlap = (
                len(claim_tokens & evidence_text_tokens) / len(evidence_text_tokens)
                if evidence_text_tokens
                else 0.0
            )
            number_match = bool(claim_numbers & finding_numbers)
            concept_match = len(claim_concepts & finding_concepts)
            entity_match = bool(claim_entities & finding_entities)
            time_match = bool(claim_times & finding_times)

            # Structured signals are the primary match for paraphrased reports:
            # same number + same business concept/entity/time is much more stable
            # than Chinese character-token overlap.
            if number_match and (concept_match or entity_match or time_match):
                structured_score = 1.0
            elif number_match:
                structured_score = 0.7
            elif concept_match >= 2 or (entity_match and time_match):
                structured_score = 0.6
            elif concept_match and (entity_match or time_match):
                structured_score = 0.5
            else:
                structured_score = 0.0

            if overlap >= 0.45 or evidence_overlap >= 0.35 or structured_score >= 0.5:
                matched_findings.append((overlap, evidence_overlap, structured_score, finding))

        matched_findings.sort(
            key=lambda item: (
                -(item[0] + item[1] + item[2]),
                str(_value(item[3], "finding_id")),
            )
        )
        selected = matched_findings[:3]
        finding_ids: list[str] = []
        evidence_ids: list[str] = []
        verified_count = 0

        for _, _, _, finding in selected:
            finding_id = str(_value(finding, "finding_id"))
            if finding_id and finding_id not in finding_ids:
                finding_ids.append(finding_id)

            supported_ids = [str(item) for item in (_value(finding, "supported_by") or [])]
            for item in (_value(finding, "evidence") or []):
                evidence_id = str(_value(item, "evidence_id"))
                if evidence_id:
                    supported_ids.append(evidence_id)

            for evidence_id in supported_ids:
                if not evidence_id or evidence_id in evidence_ids:
                    continue
                evidence_ids.append(evidence_id)
                # Never fall back to a stale loop variable.  Resolve explicitly
                # from Runtime evidence first, then the finding's embedded copy.
                source = evidence_items.get(evidence_id)
                if source is None:
                    source = next(
                        (
                            item
                            for item in finding_evidence_by_id.get(finding_id, [])
                            if str(_value(item, "evidence_id")) == evidence_id
                        ),
                        None,
                    )
                if source is not None and str(_value(source, "verification_status")) == "verified":
                    verified_count += 1

        denominator = max(1, len(selected))
        confidence = min(
            1.0,
            (
                sum(overlap + evidence_overlap + structured for overlap, evidence_overlap, structured, _ in selected)
                / denominator
            )
            * (1.0 if verified_count and evidence_ids else 0.8),
        )
        raw = f"{normalized}|{','.join(finding_ids)}|{','.join(evidence_ids)}"
        claims.append(
            ReportClaim(
                claim_id=f"claim_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}",
                text=normalized,
                finding_ids=finding_ids,
                evidence_ids=evidence_ids,
                confidence=round(confidence, 4),
                verified=bool(evidence_ids) and verified_count == len(evidence_ids),
                metadata={"matched_finding_count": len(selected)},
            )
        )
    return claims


def trace_claim_lineage(
    claim: ReportClaim,
    findings: Iterable[Any],
    evidence: Iterable[Any],
    artifacts: Iterable[Any],
    executions: Iterable[Any],
) -> ClaimLineage:
    """Expand a claim using claim.evidence_ids as the authoritative entry point."""
    finding_map = {str(_value(item, "finding_id")): item for item in (findings or [])}
    evidence_map = {
        str(_value(item, "evidence_id")): item
        for item in (evidence or [])
        if _value(item, "evidence_id") is not None
    }
    artifact_map = {
        str(_value(item, "artifact_id")): item
        for item in (artifacts or [])
        if _value(item, "artifact_id") is not None
    }
    execution_map = {
        str(_value(item, "execution_id")): item
        for item in (executions or [])
        if _value(item, "execution_id") is not None
    }

    evidence_ids: list[str] = []
    for evidence_id in claim.evidence_ids:
        evidence_id = str(evidence_id)
        if evidence_id and evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)

    finding_ids: list[str] = []
    artifact_ids: list[str] = []
    execution_ids: list[str] = []
    skill_names: list[str] = []

    def _expand_evidence(evidence_id: str, source: Any) -> None:
        for artifact_id in (_value(source, "source_artifact_ids") or []):
            artifact_id = str(artifact_id)
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            artifact = artifact_map.get(artifact_id)
            if artifact is None:
                continue
            artifact_execution_id = str(_value(artifact, "execution_id") or "")
            if artifact_execution_id and artifact_execution_id not in execution_ids:
                execution_ids.append(artifact_execution_id)

        for execution_id in (_value(source, "source_execution_ids") or []):
            execution_id = str(execution_id)
            if execution_id and execution_id not in execution_ids:
                execution_ids.append(execution_id)
        skill_name = str(_value(source, "skill_name") or "")
        if skill_name and skill_name not in skill_names:
            skill_names.append(skill_name)

    # Authoritative path: claim.evidence_ids → Runtime Evidence.
    for evidence_id in evidence_ids:
        source = evidence_map.get(evidence_id)
        if source is not None:
            _expand_evidence(evidence_id, source)

    # Supplement with Finding.evidence, especially for legacy claims that may
    # contain an embedded evidence object without a Runtime map entry.
    for finding_id in claim.finding_ids:
        if finding_id not in finding_ids:
            finding_ids.append(finding_id)
        finding = finding_map.get(finding_id)
        if finding is None:
            continue
        for item in (_value(finding, "evidence") or []):
            evidence_id = str(_value(item, "evidence_id"))
            if not evidence_id:
                continue
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            source = evidence_map.get(evidence_id, item)
            _expand_evidence(evidence_id, source)

    # Complete Artifact provenance through Execution outputs.
    for execution_id in list(execution_ids):
        execution = execution_map.get(execution_id)
        if execution is None:
            continue
        for artifact_id in (_value(execution, "output_artifact_ids") or []):
            artifact_id = str(artifact_id)
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)

    for execution_id in execution_ids:
        execution = execution_map.get(execution_id)
        skill_name = str(_value(execution, "skill_name") or "")
        if skill_name and skill_name not in skill_names:
            skill_names.append(skill_name)

    missing: list[str] = []
    if not finding_ids:
        missing.append("finding")
    if not evidence_ids:
        missing.append("evidence")
    if not artifact_ids:
        missing.append("artifact")
    if not execution_ids:
        missing.append("execution")

    return ClaimLineage(
        claim_id=claim.claim_id,
        finding_ids=finding_ids,
        evidence_ids=evidence_ids,
        artifact_ids=artifact_ids,
        execution_ids=execution_ids,
        skill_names=skill_names,
        complete=not missing,
        missing=missing,
    )
