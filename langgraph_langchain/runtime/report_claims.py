"""Claim-level provenance for generated analysis reports."""

from __future__ import annotations

import hashlib
import re
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
    evidence_items = {
        str(_value(item, "evidence_id")): item
        for item in (evidence or [])
    }
    claims: list[ReportClaim] = []
    seen_text: set[str] = set()

    for text in _claim_candidates(report_text):
        normalized = re.sub(r"\s+", " ", text)
        if normalized.casefold() in seen_text:
            continue
        seen_text.add(normalized.casefold())

        claim_tokens = _tokens(normalized)
        matched_findings: list[Any] = []
        for finding in finding_items:
            statement_tokens = _tokens(_value(finding, "statement"))
            if not statement_tokens:
                continue
            overlap = len(claim_tokens & statement_tokens) / len(statement_tokens)
            evidence_text_tokens = set()
            for item in (_value(finding, "evidence") or []):
                evidence_text_tokens.update(_tokens(_value(item, "evidence_text")))
            evidence_overlap = len(claim_tokens & evidence_text_tokens) / len(evidence_text_tokens) if evidence_text_tokens else 0.0
            if overlap >= 0.45 or evidence_overlap >= 0.35:
                matched_findings.append((overlap, evidence_overlap, finding))

        matched_findings.sort(
            key=lambda item: (-(item[0] + item[1]), str(_value(item[2], "finding_id")))
        )
        selected = matched_findings[:3]
        finding_ids: list[str] = []
        evidence_ids: list[str] = []
        verified_count = 0

        for _, _, finding in selected:
            finding_id = str(_value(finding, "finding_id"))
            if finding_id and finding_id not in finding_ids:
                finding_ids.append(finding_id)
            supported_ids = [
                str(item) for item in (_value(finding, "supported_by") or [])
            ]
            for item in (_value(finding, "evidence") or []):
                supported_ids.append(str(_value(item, "evidence_id")))
            for evidence_id in supported_ids:
                if not evidence_id or evidence_id in evidence_ids:
                    continue
                evidence_ids.append(evidence_id)
                source = evidence_items.get(evidence_id, item)
                if str(_value(source, "verification_status")) == "verified":
                    verified_count += 1

        denominator = max(1, len(selected))
        confidence = min(
            1.0,
            (sum(overlap + evidence_overlap for overlap, evidence_overlap, _ in selected) / denominator)
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
    """Expand one report claim through Finding → Evidence → Artifact → Execution."""
    finding_map = {str(_value(item, "finding_id")): item for item in (findings or [])}
    evidence_map = {str(_value(item, "evidence_id")): item for item in (evidence or [])}
    artifact_map = {str(_value(item, "artifact_id")): item for item in (artifacts or [])}
    execution_map = {str(_value(item, "execution_id")): item for item in (executions or [])}

    artifact_ids: list[str] = []
    execution_ids: list[str] = []
    skill_names: list[str] = []

    for finding_id in claim.finding_ids:
        finding = finding_map.get(finding_id)
        if finding is None:
            continue
        for item in (_value(finding, "evidence") or []):
            evidence_id = str(_value(item, "evidence_id"))
            source = evidence_map.get(evidence_id, item)
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

    # Some Evidence cites its Execution but not every Artifact directly.  Use the
    # Execution's own output lineage to complete the provenance chain.
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
    if not claim.finding_ids:
        missing.append("finding")
    if not claim.evidence_ids:
        missing.append("evidence")
    if not artifact_ids:
        missing.append("artifact")
    if not execution_ids:
        missing.append("execution")

    return ClaimLineage(
        claim_id=claim.claim_id,
        finding_ids=list(claim.finding_ids),
        evidence_ids=list(claim.evidence_ids),
        artifact_ids=artifact_ids,
        execution_ids=execution_ids,
        skill_names=skill_names,
        complete=not missing,
        missing=missing,
    )
