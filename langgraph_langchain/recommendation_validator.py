"""
Recommendation validator for Week 3: Reviewable Governance

This module validates that recommendations are properly backed by evidence.
"""

from typing import List, Tuple, Dict
from langgraph_langchain.schemas import Finding


class RecommendationType:
    """Recommendation types based on evidence strength."""
    IMMEDIATE_ACTION = "immediate_action"  # Strong evidence, clear impact
    VALIDATION = "validation"  # Medium evidence, needs verification
    OBSERVATION = "observation"  # Weak evidence, monitor only


def classify_recommendation_type(finding: Finding) -> str:
    """
    Classify what type of recommendation is appropriate based on evidence strength.

    Returns:
        RecommendationType constant
    """
    evidence_level = finding.evidence_level
    confidence_level = finding.confidence_level
    evidence_count = len(finding.evidence)

    # Immediate action requires:
    # - High confidence
    # - Level B or C evidence
    # - Multiple pieces of evidence
    if (confidence_level == "high" and
        evidence_level in ["B", "C"] and
        evidence_count >= 2):
        return RecommendationType.IMMEDIATE_ACTION

    # Validation type for medium evidence
    elif (confidence_level in ["medium", "high"] and
          evidence_level in ["B", "C"] and
          evidence_count >= 1):
        return RecommendationType.VALIDATION

    # Observation type for weak evidence
    else:
        return RecommendationType.OBSERVATION


def validate_recommendation_language(
    recommendation_text: str,
    finding: Finding
) -> Tuple[bool, str]:
    """
    Validate that recommendation language matches evidence strength.

    Args:
        recommendation_text: The recommendation text to validate
        finding: The finding that supports this recommendation

    Returns:
        (is_valid, error_message)
    """
    rec_type = classify_recommendation_type(finding)
    rec_lower = recommendation_text.lower()

    # Strong action keywords
    strong_action_keywords = [
        "should", "must", "prioritize", "increase", "cut", "stop", "launch",
        "立即", "全面推广", "马上", "立刻", "直接上线", "全面实施",
        "立即调整", "应该", "必须", "优先", "增加", "减少", "停止", "启动"
    ]

    # Validation keywords
    validation_keywords = [
        "验证", "validate", "verify", "test", "experiment", "试验",
        "进一步分析", "further analysis", "investigate", "调查"
    ]

    # Observation keywords
    observation_keywords = [
        "观察", "monitor", "observe", "track", "watch", "follow",
        "持续监测", "继续关注", "keep monitoring"
    ]

    has_strong_action = any(kw in rec_lower for kw in strong_action_keywords)
    has_validation = any(kw in rec_lower for kw in validation_keywords)
    has_observation = any(kw in rec_lower for kw in observation_keywords)

    # Validate based on recommendation type
    if rec_type == RecommendationType.IMMEDIATE_ACTION:
        # Strong evidence allows strong action
        return True, ""

    elif rec_type == RecommendationType.VALIDATION:
        # Medium evidence should use validation language
        if has_strong_action and not (has_validation or has_observation):
            return False, (
                f"Recommendation uses strong action language but finding '{finding.finding_id}' "
                f"has only {finding.confidence_level} confidence and {len(finding.evidence)} evidence item(s). "
                f"Use validation language instead: '建议验证...', 'suggest testing...', '进一步分析...'"
            )
        return True, ""

    elif rec_type == RecommendationType.OBSERVATION:
        # Weak evidence should only observe
        if has_strong_action or has_validation:
            return False, (
                f"Recommendation uses action/validation language but finding '{finding.finding_id}' "
                f"has only {finding.confidence_level} confidence with evidence_level={finding.evidence_level}. "
                f"Use observation language instead: '持续观察...', 'continue monitoring...', '建立预警...'"
            )
        return True, ""

    return True, ""


def extract_recommendations_from_report(report_markdown: str) -> List[str]:
    """
    Extract recommendation bullets from the report.

    Returns:
        List of recommendation text strings
    """
    recommendations = []
    in_recommendations_section = False

    for line in report_markdown.split("\n"):
        line_stripped = line.strip()

        # Check if we're entering recommendations section
        if line_stripped.startswith("#") and any(
            kw in line_stripped.lower()
            for kw in ["recommendation", "建议", "行动建议"]
        ):
            in_recommendations_section = True
            continue

        # Check if we're leaving recommendations section (next heading)
        if in_recommendations_section and line_stripped.startswith("#"):
            break

        # Extract bullet points in recommendations section
        if in_recommendations_section and line_stripped.startswith(("- ", "* ")):
            recommendations.append(line_stripped[2:])

    return recommendations


def validate_recommendations_against_findings(
    report_markdown: str,
    findings: List[Finding]
) -> List[str]:
    """
    Validate that all recommendations in the report are backed by appropriate evidence.

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []

    recommendations = extract_recommendations_from_report(report_markdown)

    if not recommendations:
        # No recommendations section - that's OK
        return []

    # Check if any recommendations use strong action language
    strong_action_keywords = [
        "should", "must", "prioritize", "increase", "cut", "stop", "launch",
        "立即", "全面推广", "马上", "立刻", "直接上线", "全面实施"
    ]

    has_strong_recommendations = any(
        any(kw in rec.lower() for kw in strong_action_keywords)
        for rec in recommendations
    )

    if has_strong_recommendations:
        # Check if we have sufficient evidence for strong recommendations
        immediate_action_findings = [
            f for f in findings
            if classify_recommendation_type(f) == RecommendationType.IMMEDIATE_ACTION
        ]

        if not immediate_action_findings:
            errors.append(
                "Report contains strong action recommendations but no findings have sufficient "
                "evidence strength (high confidence + level B/C + multiple evidence items). "
                "Either strengthen the evidence or downgrade recommendations to validation/observation language."
            )

    return errors


def generate_recommendation_template(finding: Finding) -> str:
    """
    Generate a recommendation template based on finding evidence strength.

    Returns:
        Template text showing appropriate recommendation language
    """
    rec_type = classify_recommendation_type(finding)

    if rec_type == RecommendationType.IMMEDIATE_ACTION:
        return (
            f"Based on finding {finding.finding_id} (high confidence, strong evidence):\n"
            f"- Immediate action: [具体行动建议]\n"
            f"- Expected impact: [预期影响]\n"
            f"- Implementation: [实施方案]"
        )

    elif rec_type == RecommendationType.VALIDATION:
        return (
            f"Based on finding {finding.finding_id} (medium evidence):\n"
            f"- Validation needed: [需要验证的假设]\n"
            f"- Suggested analysis: [建议的分析方法]\n"
            f"- Data to collect: [需要补充的数据]"
        )

    else:  # OBSERVATION
        return (
            f"Based on finding {finding.finding_id} (weak evidence):\n"
            f"- Continue monitoring: [持续观察的指标]\n"
            f"- Alert threshold: [预警阈值]\n"
            f"- Review period: [复核周期]"
        )
