"""
Evidence level validator for Week 3: Reviewable Governance

This module validates that causal language is only used when evidence meets threshold.
"""

import re
from typing import List, Tuple

from langgraph_langchain.schemas import Finding


# Causal keywords that require C-level evidence
CAUSAL_KEYWORDS = [
    "原因", "驱动", "导致", "根因", "引起", "造成", "促使",
    "因为", "由于", "归因于", "源于", "来自于",
    "caused", "driven", "due to", "because of", "resulted from",
    "led to", "leads to", "cause", "drive", "result from"
]

# Correlation keywords that require B-level evidence
CORRELATION_KEYWORDS = [
    "相关", "关联", "同时", "伴随", "一致",
    "correlated", "associated", "concurrent", "aligned"
]


def check_causal_language(text: str) -> List[str]:
    """Check if text contains causal language."""
    found = []
    for keyword in CAUSAL_KEYWORDS:
        if keyword in text.lower():
            found.append(keyword)
    return found


def check_correlation_language(text: str) -> List[str]:
    """Check if text contains correlation language."""
    found = []
    for keyword in CORRELATION_KEYWORDS:
        if keyword in text.lower():
            found.append(keyword)
    return found


def validate_evidence_level(finding: Finding) -> Tuple[bool, str]:
    """
    Validate that the evidence level matches the language used in the statement.

    Returns:
        (is_valid, error_message)
    """
    statement = finding.statement
    evidence_level = finding.evidence_level

    # Check for causal language
    causal_words = check_causal_language(statement)
    if causal_words:
        # Causal language requires C-level evidence
        if evidence_level != "C":
            return False, (
                f"Finding '{finding.finding_id}' uses causal language ({', '.join(causal_words)}) "
                f"but has evidence_level='{evidence_level}'. Causal claims require evidence_level='C' "
                f"and must meet stricter criteria (temporal ordering, control groups, mechanism explanation, "
                f"alternative explanations ruled out). Either downgrade the language to correlation/observation "
                f"or provide stronger evidence."
            )

        # For C-level, check if evidence is sufficient
        if len(finding.evidence) < 2:
            return False, (
                f"Finding '{finding.finding_id}' claims causal relationship but only has {len(finding.evidence)} "
                f"evidence item(s). Causal claims require at least 2 pieces of evidence showing temporal ordering, "
                f"mechanism, or control group comparison."
            )

    # Check for correlation language
    correlation_words = check_correlation_language(statement)
    if correlation_words and evidence_level == "A":
        return False, (
            f"Finding '{finding.finding_id}' uses correlation language ({', '.join(correlation_words)}) "
            f"but has evidence_level='A'. Correlation claims require at least evidence_level='B'. "
            f"Level A is for pure factual descriptions without relationship claims."
        )

    # A-level should not make relationship claims
    if evidence_level == "A":
        # Check for any relationship language
        relationship_patterns = [
            r"高于", r"低于", r"超过", r"不足",
            r"增长", r"下降", r"上升", r"减少",
            r"higher than", r"lower than", r"increased", r"decreased"
        ]
        for pattern in relationship_patterns:
            if re.search(pattern, statement, re.IGNORECASE):
                # This is OK for A-level if it's just stating facts
                pass

    return True, ""


def validate_findings_evidence_levels(findings: List[Finding]) -> List[str]:
    """
    Validate all findings' evidence levels.

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []

    for finding in findings:
        is_valid, error_msg = validate_evidence_level(finding)
        if not is_valid:
            errors.append(error_msg)

    return errors


def suggest_evidence_level(statement: str, evidence_count: int) -> str:
    """
    Suggest appropriate evidence level based on statement language.

    Returns:
        Suggested evidence level: "A", "B", or "C"
    """
    causal_words = check_causal_language(statement)
    correlation_words = check_correlation_language(statement)

    if causal_words:
        return "C"
    elif correlation_words or evidence_count >= 2:
        return "B"
    else:
        return "A"
