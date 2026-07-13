"""Evidence binding validation for findings.

This module ensures that every finding has proper evidence backing,
and that evidence references valid artifacts.
"""

from typing import List, Optional, Tuple

from langgraph_langchain.schemas import EvidenceItem, Finding


class EvidenceBindingError(Exception):
    """Raised when evidence binding validation fails."""
    pass


def validate_evidence_binding(
    finding: Finding,
    available_artifacts: dict,
) -> Tuple[bool, List[str]]:
    """Validate that a finding has proper evidence binding.

    Args:
        finding: The finding to validate
        available_artifacts: Dict of artifact_id -> ArtifactRef

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Rule 1: Every finding must have at least one evidence item
    if not finding.evidence:
        errors.append(
            f"Finding {finding.finding_id} has no evidence. "
            "Every finding must be backed by at least one evidence item."
        )
        return False, errors

    # Rule 2: Every evidence item must have non-empty evidence_text
    for idx, evidence in enumerate(finding.evidence):
        if not evidence.evidence_text or not evidence.evidence_text.strip():
            errors.append(
                f"Finding {finding.finding_id}, evidence #{idx+1}: "
                "evidence_text is empty. Provide concrete evidence description."
            )

    # Rule 3: Evidence referencing artifacts must point to valid artifacts
    for idx, evidence in enumerate(finding.evidence):
        for artifact_name in evidence.source_artifacts:
            if artifact_name not in available_artifacts:
                errors.append(
                    f"Finding {finding.finding_id}, evidence #{idx+1}: "
                    f"references unknown artifact '{artifact_name}'. "
                    "Ensure artifact is created before referencing it."
                )

    # Rule 4: High confidence findings should have stronger evidence
    if finding.confidence_level == "high":
        # Should have multiple evidence items or detailed stats
        if len(finding.evidence) < 2:
            has_stats = any(
                e.stats and len(e.stats) >= 2 for e in finding.evidence
            )
            has_artifacts = any(
                e.source_artifacts for e in finding.evidence
            )
            if not (has_stats or has_artifacts):
                errors.append(
                    f"Finding {finding.finding_id} has high confidence but weak evidence. "
                    "High confidence findings should have: multiple evidence items, "
                    "detailed stats, or artifact references."
                )

    # Rule 5: Causal claims (evidence_level=C) require strong evidence
    if finding.evidence_level == "C":
        # Must have calculation_method or detailed stats
        has_method = any(
            e.calculation_method for e in finding.evidence
        )
        has_detailed_stats = any(
            e.stats and len(e.stats) >= 3 for e in finding.evidence
        )
        if not (has_method or has_detailed_stats):
            errors.append(
                f"Finding {finding.finding_id} makes causal claim (level C) "
                "but lacks strong evidence. Causal claims require: "
                "calculation_method or detailed stats (3+ metrics)."
            )

    return len(errors) == 0, errors


def validate_evidence_completeness(
    finding: Finding,
    available_artifacts: dict,
) -> Tuple[bool, List[str]]:
    """Check if evidence is complete and well-structured.

    This is a softer check than validate_evidence_binding.
    Returns warnings rather than hard errors.

    Args:
        finding: The finding to validate
        available_artifacts: Dict of artifact_id -> ArtifactRef

    Returns:
        Tuple of (is_complete, list_of_warnings)
    """
    warnings = []

    # Check 1: Evidence should reference source fields
    for idx, evidence in enumerate(finding.evidence):
        if not evidence.source_fields:
            warnings.append(
                f"Finding {finding.finding_id}, evidence #{idx+1}: "
                "no source_fields specified. Consider documenting which data fields "
                "were used to derive this evidence."
            )

    # Check 2: Quantitative findings should have stats
    quantitative_keywords = ["increase", "decrease", "percent", "%", "ratio", "rate"]
    if any(kw in finding.statement.lower() for kw in quantitative_keywords):
        has_stats = any(e.stats for e in finding.evidence)
        if not has_stats:
            warnings.append(
                f"Finding {finding.finding_id} appears quantitative "
                "but has no stats in evidence. Consider adding stats dict "
                "with key metrics."
            )

    # Check 3: Time-based findings should have time_window
    time_keywords = ["trend", "over time", "period", "quarter", "month", "year"]
    if any(kw in finding.statement.lower() for kw in time_keywords):
        has_time_window = any(e.time_window for e in finding.evidence)
        if not has_time_window:
            warnings.append(
                f"Finding {finding.finding_id} appears time-based "
                "but has no time_window in evidence. Consider specifying "
                "the time period."
            )

    # Check 4: Grouped findings should have group_dimension
    group_keywords = ["by region", "by category", "by type", "per", "each"]
    if any(kw in finding.statement.lower() for kw in group_keywords):
        has_group_dim = any(e.group_dimension for e in finding.evidence)
        if not has_group_dim:
            warnings.append(
                f"Finding {finding.finding_id} appears to involve grouping "
                "but has no group_dimension in evidence. Consider specifying "
                "the grouping dimension."
            )

    return len(warnings) == 0, warnings


def get_evidence_summary(finding: Finding) -> str:
    """Generate a human-readable summary of evidence for a finding.

    Args:
        finding: The finding to summarize

    Returns:
        Multi-line string summarizing the evidence
    """
    lines = [f"Evidence for {finding.finding_id}: {finding.statement}"]
    lines.append(f"  Confidence: {finding.confidence_level}, Level: {finding.evidence_level}")
    lines.append(f"  Evidence items: {len(finding.evidence)}")

    for idx, evidence in enumerate(finding.evidence, 1):
        lines.append(f"\n  Evidence #{idx}:")
        lines.append(f"    Text: {evidence.evidence_text[:100]}...")

        if evidence.source_fields:
            lines.append(f"    Fields: {', '.join(evidence.source_fields)}")

        if evidence.source_artifacts:
            lines.append(f"    Artifacts: {', '.join(evidence.source_artifacts)}")

        if evidence.stats:
            stats_str = ", ".join(f"{k}={v}" for k, v in list(evidence.stats.items())[:3])
            lines.append(f"    Stats: {stats_str}")

        if evidence.time_window:
            lines.append(f"    Time: {evidence.time_window}")

        if evidence.group_dimension:
            lines.append(f"    Group: {evidence.group_dimension}")

    return "\n".join(lines)
