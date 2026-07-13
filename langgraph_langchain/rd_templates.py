"""
R&D Efficiency Analysis Templates

This module provides reusable analysis templates for common R&D efficiency scenarios.
Each template includes:
- Pre-defined analysis steps
- Required data columns
- Key metrics to calculate
- Common findings patterns
- Report structure
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class AnalysisTemplate(BaseModel):
    """A reusable analysis template."""
    template_id: str
    template_name: str
    description: str
    target_user: str  # "data_analyst", "engineering_manager", "product_manager"
    difficulty: str  # "easy", "medium", "hard"
    required_data: List[str]  # Required data columns
    optional_data: List[str] = Field(default_factory=list)
    key_metrics: List[str]
    analysis_steps: List[str]
    expected_findings: List[str]
    report_sections: List[str]
    example_questions: List[str]
    caveats: List[str] = Field(default_factory=list)


# ============================================================================
# Sprint Retrospective Templates
# ============================================================================

SPRINT_RETRO_TEMPLATE = AnalysisTemplate(
    template_id="RD-T001",
    template_name="Sprint Retrospective Analysis",
    description="Analyze sprint performance to identify what went well and what needs improvement",
    target_user="data_analyst",
    difficulty="easy",
    required_data=[
        "sprint",
        "story_points",
        "status",
        "work_item_type",
        "started_date",
        "completed_date"
    ],
    optional_data=[
        "assignee",
        "priority",
        "component",
        "blocked_days"
    ],
    key_metrics=[
        "Story Points Completed",
        "Sprint Commitment Accuracy",
        "Cycle Time",
        "Throughput"
    ],
    analysis_steps=[
        "1. Calculate total story points completed vs committed",
        "2. Compare velocity to previous 3 sprints",
        "3. Analyze cycle time distribution by work item type",
        "4. Identify items that took longer than expected",
        "5. Check for blockers or impediments",
        "6. Segment analysis by work item type (feature/bug/tech debt)"
    ],
    expected_findings=[
        "Sprint commitment accuracy (% of committed work completed)",
        "Velocity trend (increasing/stable/decreasing)",
        "Work item types that took longest",
        "Blockers or impediments that affected delivery"
    ],
    report_sections=[
        "Executive Summary",
        "Sprint Commitment",
        "Velocity Trend",
        "Cycle Time Analysis",
        "Blockers and Impediments",
        "Recommendations"
    ],
    example_questions=[
        "Did we meet our sprint commitment?",
        "How does our velocity compare to previous sprints?",
        "Which types of work took longer than expected?",
        "What blocked us during the sprint?"
    ],
    caveats=[
        "Velocity is team-specific - do not compare across teams",
        "One sprint is not enough for trend analysis",
        "Consider external factors (holidays, team changes)"
    ]
)


VELOCITY_TREND_TEMPLATE = AnalysisTemplate(
    template_id="RD-T002",
    template_name="Velocity Trend Analysis",
    description="Track velocity over time to identify trends and predict future capacity",
    target_user="engineering_manager",
    difficulty="medium",
    required_data=[
        "sprint",
        "story_points",
        "status",
        "team"
    ],
    optional_data=[
        "team_size",
        "sprint_duration_days"
    ],
    key_metrics=[
        "Story Points Completed",
        "Velocity Trend",
        "Velocity Stability"
    ],
    analysis_steps=[
        "1. Calculate velocity for each sprint (last 6-12 sprints)",
        "2. Plot velocity trend over time",
        "3. Calculate moving average (3-sprint window)",
        "4. Identify outliers and investigate causes",
        "5. Normalize by team size if available",
        "6. Calculate velocity stability (coefficient of variation)"
    ],
    expected_findings=[
        "Velocity trend direction (increasing/stable/decreasing)",
        "Velocity stability (high/medium/low variability)",
        "Outlier sprints and their causes",
        "Predicted velocity for next sprint"
    ],
    report_sections=[
        "Executive Summary",
        "Velocity Trend",
        "Velocity Stability",
        "Outlier Analysis",
        "Capacity Prediction"
    ],
    example_questions=[
        "Is our velocity increasing or decreasing?",
        "How stable is our velocity?",
        "What caused velocity spikes or drops?",
        "What velocity should we plan for next sprint?"
    ],
    caveats=[
        "Velocity inflation can occur if estimation becomes looser",
        "Team changes affect velocity",
        "Velocity is not a productivity metric"
    ]
)


# ============================================================================
# Quality Analysis Templates
# ============================================================================

QUALITY_TREND_TEMPLATE = AnalysisTemplate(
    template_id="RD-T003",
    template_name="Code Quality Trend Analysis",
    description="Track quality metrics over time to identify degradation or improvement",
    target_user="engineering_manager",
    difficulty="medium",
    required_data=[
        "sprint",
        "work_item_type",
        "status",
        "severity"
    ],
    optional_data=[
        "component",
        "root_cause",
        "found_in_stage",
        "test_coverage"
    ],
    key_metrics=[
        "Defect Rate",
        "Escaped Defects",
        "Test Coverage",
        "Defect Density by Component"
    ],
    analysis_steps=[
        "1. Separate features and bugs in the data",
        "2. Calculate defect rate per sprint (bugs/features)",
        "3. Identify escaped defects (found in production)",
        "4. Analyze defect distribution by component",
        "5. Track test coverage trend if available",
        "6. Segment by severity (P0/P1/P2)"
    ],
    expected_findings=[
        "Defect rate trend (improving/stable/degrading)",
        "Components with highest defect density",
        "Escaped defect count and severity",
        "Correlation between test coverage and defect rate"
    ],
    report_sections=[
        "Executive Summary",
        "Defect Rate Trend",
        "Escaped Defects Analysis",
        "Component Quality Breakdown",
        "Test Coverage Analysis",
        "Recommendations"
    ],
    example_questions=[
        "Is our defect rate increasing?",
        "Which components have the most quality issues?",
        "Are we catching defects before production?",
        "Is test coverage improving?"
    ],
    caveats=[
        "Defect rate may increase when testing improves",
        "Definition of 'defect' varies by team",
        "Severity matters - not all defects are equal"
    ]
)


ESCAPED_DEFECTS_TEMPLATE = AnalysisTemplate(
    template_id="RD-T004",
    template_name="Escaped Defects Root Cause Analysis",
    description="Deep dive into defects that escaped to production to identify gaps",
    target_user="data_analyst",
    difficulty="hard",
    required_data=[
        "bug_id",
        "severity",
        "found_in_stage",
        "created_date",
        "component"
    ],
    optional_data=[
        "root_cause",
        "related_feature",
        "test_coverage",
        "code_review_comments"
    ],
    key_metrics=[
        "Escaped Defects",
        "Escaped Defect Rate",
        "Mean Time to Detect",
        "Customer Impact"
    ],
    analysis_steps=[
        "1. Filter for defects found in production",
        "2. Segment by severity (P0/P1/P2)",
        "3. Analyze root causes if available",
        "4. Identify components with most escaped defects",
        "5. Calculate time from release to detection",
        "6. Correlate with test coverage or code review metrics"
    ],
    expected_findings=[
        "Count and rate of escaped defects",
        "Most common root causes",
        "Components with highest escape rate",
        "Gaps in testing or code review process"
    ],
    report_sections=[
        "Executive Summary",
        "Escaped Defects Overview",
        "Root Cause Analysis",
        "Component Breakdown",
        "Process Gaps Identified",
        "Recommendations"
    ],
    example_questions=[
        "How many defects escaped to production?",
        "What are the common root causes?",
        "Which components have the most escapes?",
        "What process gaps led to these escapes?"
    ],
    caveats=[
        "Root cause analysis requires manual investigation",
        "Not all escaped defects are equally critical",
        "Focus on preventing high-severity escapes"
    ]
)


# ============================================================================
# Delivery Performance Templates
# ============================================================================

CYCLE_TIME_ANALYSIS_TEMPLATE = AnalysisTemplate(
    template_id="RD-T005",
    template_name="Cycle Time Analysis",
    description="Analyze cycle time to identify bottlenecks and improve flow",
    target_user="engineering_manager",
    difficulty="medium",
    required_data=[
        "work_item_id",
        "work_item_type",
        "started_date",
        "completed_date",
        "status"
    ],
    optional_data=[
        "assignee",
        "component",
        "story_points",
        "blocked_days"
    ],
    key_metrics=[
        "Cycle Time",
        "Lead Time",
        "Work in Progress",
        "Throughput"
    ],
    analysis_steps=[
        "1. Calculate cycle time for each completed item",
        "2. Calculate median and P90 cycle time",
        "3. Segment by work item type (feature/bug/tech debt)",
        "4. Identify items with longest cycle time",
        "5. Analyze correlation with story points or complexity",
        "6. Check for blocked items and calculate blocked time"
    ],
    expected_findings=[
        "Median and P90 cycle time by work item type",
        "Items with unusually long cycle time",
        "Correlation between size and cycle time",
        "Blockers that extended cycle time"
    ],
    report_sections=[
        "Executive Summary",
        "Cycle Time Distribution",
        "Bottleneck Analysis",
        "Work Item Type Comparison",
        "Blocker Impact",
        "Recommendations"
    ],
    example_questions=[
        "What is our typical cycle time?",
        "Which items took longest to complete?",
        "Do larger items take proportionally longer?",
        "What are the main bottlenecks?"
    ],
    caveats=[
        "Use median, not mean, for cycle time",
        "Exclude weekends and holidays",
        "Segment by work item type and size"
    ]
)


DEPLOYMENT_FREQUENCY_TEMPLATE = AnalysisTemplate(
    template_id="RD-T006",
    template_name="Deployment Frequency and Stability",
    description="Analyze deployment frequency and change failure rate (DORA metrics)",
    target_user="engineering_manager",
    difficulty="medium",
    required_data=[
        "deployment_id",
        "deployment_date",
        "status"
    ],
    optional_data=[
        "environment",
        "change_count",
        "rollback_reason"
    ],
    key_metrics=[
        "Deployment Frequency",
        "Change Failure Rate",
        "Mean Time to Recovery"
    ],
    analysis_steps=[
        "1. Calculate deployments per week/month",
        "2. Identify failed deployments (rollback, hotfix, incident)",
        "3. Calculate change failure rate",
        "4. Analyze failure reasons if available",
        "5. Track trend over time (last 3-6 months)",
        "6. Compare to DORA benchmarks"
    ],
    expected_findings=[
        "Deployment frequency (per week)",
        "Change failure rate (%)",
        "Trend over time (improving/stable/degrading)",
        "Common failure reasons"
    ],
    report_sections=[
        "Executive Summary",
        "Deployment Frequency",
        "Change Failure Rate",
        "Failure Analysis",
        "DORA Benchmark Comparison",
        "Recommendations"
    ],
    example_questions=[
        "How often do we deploy?",
        "What percentage of deployments fail?",
        "Are we improving over time?",
        "What causes deployment failures?"
    ],
    caveats=[
        "Higher deployment frequency is generally better",
        "Change failure rate should be <15%",
        "Hotfixes should be tracked separately"
    ]
)


# ============================================================================
# Collaboration Templates
# ============================================================================

PR_REVIEW_ANALYSIS_TEMPLATE = AnalysisTemplate(
    template_id="RD-T007",
    template_name="Pull Request Review Analysis",
    description="Analyze PR review patterns to improve collaboration and code quality",
    target_user="engineering_manager",
    difficulty="medium",
    required_data=[
        "pr_id",
        "pr_created_at",
        "first_review_at",
        "merged_at"
    ],
    optional_data=[
        "lines_added",
        "lines_deleted",
        "author",
        "reviewer",
        "comment_count"
    ],
    key_metrics=[
        "PR Review Time",
        "PR Size",
        "Review Comments",
        "Merge Time"
    ],
    analysis_steps=[
        "1. Calculate time to first review",
        "2. Calculate time to merge",
        "3. Analyze PR size distribution",
        "4. Correlate PR size with review time",
        "5. Identify PRs with longest review time",
        "6. Analyze review participation patterns"
    ],
    expected_findings=[
        "Median time to first review",
        "Median time to merge",
        "PR size distribution",
        "Correlation between size and review time",
        "Review bottlenecks"
    ],
    report_sections=[
        "Executive Summary",
        "Review Time Analysis",
        "PR Size Analysis",
        "Review Bottlenecks",
        "Collaboration Patterns",
        "Recommendations"
    ],
    example_questions=[
        "How long does it take to get a PR reviewed?",
        "Are our PRs too large?",
        "Do larger PRs take longer to review?",
        "Who are the review bottlenecks?"
    ],
    caveats=[
        "Exclude weekends and holidays",
        "Very fast reviews may indicate rubber-stamping",
        "Large PRs (>500 lines) should be broken down"
    ]
)


# ============================================================================
# Template Registry
# ============================================================================

ALL_TEMPLATES = [
    SPRINT_RETRO_TEMPLATE,
    VELOCITY_TREND_TEMPLATE,
    QUALITY_TREND_TEMPLATE,
    ESCAPED_DEFECTS_TEMPLATE,
    CYCLE_TIME_ANALYSIS_TEMPLATE,
    DEPLOYMENT_FREQUENCY_TEMPLATE,
    PR_REVIEW_ANALYSIS_TEMPLATE,
]

TEMPLATES_BY_ID = {t.template_id: t for t in ALL_TEMPLATES}
TEMPLATES_BY_NAME = {t.template_name: t for t in ALL_TEMPLATES}


# ============================================================================
# Template Matching
# ============================================================================

def suggest_template(user_question: str, available_columns: List[str]) -> Optional[AnalysisTemplate]:
    """
    Suggest an analysis template based on user question and available data.

    Args:
        user_question: The user's analysis question
        available_columns: List of column names in the dataset

    Returns:
        Best matching template or None
    """
    question_lower = user_question.lower()

    # Match by keywords in question (prioritize question matching)
    if "sprint" in question_lower and ("retro" in question_lower or "回顾" in question_lower):
        return SPRINT_RETRO_TEMPLATE

    if "sprint" in question_lower:
        return SPRINT_RETRO_TEMPLATE

    if "velocity" in question_lower and "trend" in question_lower:
        return VELOCITY_TREND_TEMPLATE

    if "quality" in question_lower or "defect" in question_lower or "质量" in question_lower:
        if "escaped" in question_lower or "production" in question_lower:
            return ESCAPED_DEFECTS_TEMPLATE
        else:
            return QUALITY_TREND_TEMPLATE

    if "cycle time" in question_lower or "lead time" in question_lower:
        return CYCLE_TIME_ANALYSIS_TEMPLATE

    if "deploy" in question_lower or "dora" in question_lower:
        return DEPLOYMENT_FREQUENCY_TEMPLATE

    if "pr" in question_lower or "pull request" in question_lower or "code review" in question_lower:
        return PR_REVIEW_ANALYSIS_TEMPLATE

    # Match by available columns (only if no question match)
    if not user_question:
        if "pr_id" in available_columns and "pr_created_at" in available_columns:
            return PR_REVIEW_ANALYSIS_TEMPLATE

        if "deployment_id" in available_columns and "deployment_date" in available_columns:
            return DEPLOYMENT_FREQUENCY_TEMPLATE

        if "sprint" in available_columns and "story_points" in available_columns:
            return SPRINT_RETRO_TEMPLATE

    return None


def get_template(template_id: str) -> Optional[AnalysisTemplate]:
    """Get template by ID."""
    return TEMPLATES_BY_ID.get(template_id)


def list_templates_for_user(target_user: str) -> List[AnalysisTemplate]:
    """List all templates for a specific user type."""
    return [t for t in ALL_TEMPLATES if t.target_user == target_user]


def list_templates_by_difficulty(difficulty: str) -> List[AnalysisTemplate]:
    """List all templates of a specific difficulty."""
    return [t for t in ALL_TEMPLATES if t.difficulty == difficulty]
