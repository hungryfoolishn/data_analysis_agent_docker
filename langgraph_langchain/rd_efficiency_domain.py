"""
R&D Management Efficiency Domain Knowledge

This module defines the domain knowledge for R&D management efficiency analysis,
including key metrics, dimensions, and analysis patterns.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================================
# Core Metrics for R&D Efficiency
# ============================================================================

class RDMetricDefinition(BaseModel):
    """Definition of an R&D efficiency metric."""
    metric_name: str
    category: str  # velocity, quality, collaboration, delivery, planning
    definition: str
    calculation_method: str
    unit: str
    good_direction: str  # "higher", "lower", "stable"
    typical_range: Optional[str] = None
    caveats: List[str] = Field(default_factory=list)
    related_metrics: List[str] = Field(default_factory=list)


# Velocity Metrics
VELOCITY_METRICS = [
    RDMetricDefinition(
        metric_name="Story Points Completed",
        category="velocity",
        definition="Total story points completed in a sprint/period",
        calculation_method="SUM(story_points) WHERE status='Done' GROUP BY sprint",
        unit="points",
        good_direction="stable",
        typical_range="20-60 points per sprint for a 5-person team",
        caveats=[
            "Story points are relative, not absolute",
            "Should normalize by team size",
            "Velocity inflation can occur if estimation becomes looser",
            "Compare within same team only, not across teams"
        ],
        related_metrics=["Team Size", "Sprint Duration", "Velocity Trend"]
    ),
    RDMetricDefinition(
        metric_name="Throughput",
        category="velocity",
        definition="Number of work items completed per time period",
        calculation_method="COUNT(DISTINCT ticket_id) WHERE status='Done' GROUP BY period",
        unit="items/week",
        good_direction="higher",
        typical_range="5-15 items per week for a 5-person team",
        caveats=[
            "Does not account for item size/complexity",
            "Can be gamed by splitting work into smaller items",
            "Should segment by item type (bug vs feature vs tech debt)"
        ],
        related_metrics=["Cycle Time", "Work Item Size Distribution"]
    ),
    RDMetricDefinition(
        metric_name="Code Churn",
        category="velocity",
        definition="Lines of code added, modified, or deleted per period",
        calculation_method="SUM(lines_added + lines_modified + lines_deleted) GROUP BY period",
        unit="lines/week",
        good_direction="stable",
        typical_range="500-2000 lines per developer per week",
        caveats=[
            "High churn may indicate rework or instability",
            "Low churn may indicate blocked work or planning issues",
            "Language and codebase maturity affect typical ranges",
            "Exclude generated code and dependency updates"
        ],
        related_metrics=["Defect Rate", "Code Review Time"]
    ),
]

# Quality Metrics
QUALITY_METRICS = [
    RDMetricDefinition(
        metric_name="Defect Rate",
        category="quality",
        definition="Number of defects found per unit of work delivered",
        calculation_method="COUNT(bugs) / COUNT(features_delivered)",
        unit="defects/feature",
        good_direction="lower",
        typical_range="0.1-0.5 defects per feature",
        caveats=[
            "Definition of 'defect' varies by team",
            "Should separate by severity (P0/P1/P2)",
            "Detection time matters (found in dev vs production)",
            "May increase temporarily when testing improves"
        ],
        related_metrics=["Test Coverage", "Escaped Defects", "Mean Time to Detect"]
    ),
    RDMetricDefinition(
        metric_name="Test Coverage",
        category="quality",
        definition="Percentage of code covered by automated tests",
        calculation_method="(lines_covered / total_lines) * 100",
        unit="percentage",
        good_direction="higher",
        typical_range="60-80% for backend, 40-60% for frontend",
        caveats=[
            "High coverage does not guarantee quality tests",
            "Should focus on critical paths, not 100% coverage",
            "Exclude generated code and trivial getters/setters",
            "Branch coverage is more meaningful than line coverage"
        ],
        related_metrics=["Defect Rate", "Test Execution Time"]
    ),
    RDMetricDefinition(
        metric_name="Escaped Defects",
        category="quality",
        definition="Defects found in production that were not caught in testing",
        calculation_method="COUNT(bugs) WHERE found_in='production' AND created_after_release",
        unit="defects/release",
        good_direction="lower",
        typical_range="0-3 per release",
        caveats=[
            "Most critical quality metric",
            "Should track by severity",
            "Root cause analysis required for each escaped defect",
            "May indicate gaps in test coverage or test environment"
        ],
        related_metrics=["Test Coverage", "Mean Time to Detect", "Customer Impact"]
    ),
]

# Collaboration Metrics
COLLABORATION_METRICS = [
    RDMetricDefinition(
        metric_name="PR Review Time",
        category="collaboration",
        definition="Time from PR creation to first review or approval",
        calculation_method="MEDIAN(first_review_time - pr_created_time)",
        unit="hours",
        good_direction="lower",
        typical_range="2-8 hours for first review",
        caveats=[
            "Exclude weekends and holidays",
            "Large PRs naturally take longer",
            "Should track both first review and approval time",
            "Very fast reviews may indicate rubber-stamping"
        ],
        related_metrics=["PR Size", "Review Comments", "Merge Time"]
    ),
    RDMetricDefinition(
        metric_name="PR Size",
        category="collaboration",
        definition="Number of lines changed in a pull request",
        calculation_method="lines_added + lines_deleted",
        unit="lines",
        good_direction="lower",
        typical_range="50-300 lines per PR",
        caveats=[
            "Smaller PRs are easier to review and less risky",
            "Large PRs (>500 lines) often indicate poor decomposition",
            "Exclude generated code and dependency updates",
            "Should track distribution, not just average"
        ],
        related_metrics=["PR Review Time", "Review Comments", "Defect Rate"]
    ),
    RDMetricDefinition(
        metric_name="Knowledge Silos",
        category="collaboration",
        definition="Percentage of code files touched by only one developer",
        calculation_method="COUNT(files WHERE unique_contributors=1) / COUNT(files)",
        unit="percentage",
        good_direction="lower",
        typical_range="10-30%",
        caveats=[
            "Some specialization is natural and healthy",
            "New code naturally has fewer contributors",
            "Should track over time (e.g., 6-month window)",
            "Pair programming and code reviews help reduce silos"
        ],
        related_metrics=["Bus Factor", "Code Ownership Distribution"]
    ),
]

# Delivery Metrics
DELIVERY_METRICS = [
    RDMetricDefinition(
        metric_name="Cycle Time",
        category="delivery",
        definition="Time from work start to completion",
        calculation_method="MEDIAN(completed_date - started_date)",
        unit="days",
        good_direction="lower",
        typical_range="3-10 days for features, 1-3 days for bugs",
        caveats=[
            "Exclude time in backlog (use lead time for that)",
            "Should segment by work item type and size",
            "Exclude weekends and holidays",
            "Long cycle time may indicate blockers or scope creep"
        ],
        related_metrics=["Lead Time", "Work in Progress", "Throughput"]
    ),
    RDMetricDefinition(
        metric_name="Lead Time",
        category="delivery",
        definition="Time from work item creation to completion",
        calculation_method="MEDIAN(completed_date - created_date)",
        unit="days",
        good_direction="lower",
        typical_range="7-21 days",
        caveats=[
            "Includes time in backlog, so longer than cycle time",
            "High lead time may indicate prioritization issues",
            "Should track separately for different work types",
            "Exclude items that were deprioritized or cancelled"
        ],
        related_metrics=["Cycle Time", "Backlog Age", "Throughput"]
    ),
    RDMetricDefinition(
        metric_name="Deployment Frequency",
        category="delivery",
        definition="How often code is deployed to production",
        calculation_method="COUNT(deployments) / time_period",
        unit="deployments/week",
        good_direction="higher",
        typical_range="1-10 per week (varies by maturity)",
        caveats=[
            "DORA metric - higher is generally better",
            "Should be sustainable, not forced",
            "Requires good CI/CD and testing practices",
            "Hotfixes should be tracked separately"
        ],
        related_metrics=["Change Failure Rate", "Mean Time to Recovery"]
    ),
    RDMetricDefinition(
        metric_name="Change Failure Rate",
        category="delivery",
        definition="Percentage of deployments that cause production issues",
        calculation_method="COUNT(failed_deployments) / COUNT(deployments)",
        unit="percentage",
        good_direction="lower",
        typical_range="0-15%",
        caveats=[
            "DORA metric - lower is better",
            "Definition of 'failure' should be clear (rollback, hotfix, incident)",
            "Should track by severity",
            "May increase temporarily when deploying more frequently"
        ],
        related_metrics=["Deployment Frequency", "Mean Time to Recovery", "Escaped Defects"]
    ),
]

# Planning Metrics
PLANNING_METRICS = [
    RDMetricDefinition(
        metric_name="Sprint Commitment Accuracy",
        category="planning",
        definition="Percentage of committed work completed in sprint",
        calculation_method="(completed_points / committed_points) * 100",
        unit="percentage",
        good_direction="stable",
        typical_range="80-100%",
        caveats=[
            "Consistently 100% may indicate sandbagging",
            "Consistently <80% may indicate over-commitment or poor estimation",
            "Should track trend over time",
            "Scope changes during sprint should be tracked separately"
        ],
        related_metrics=["Velocity", "Scope Creep", "Estimation Accuracy"]
    ),
    RDMetricDefinition(
        metric_name="Estimation Accuracy",
        category="planning",
        definition="Ratio of actual time spent to estimated time",
        calculation_method="MEDIAN(actual_hours / estimated_hours)",
        unit="ratio",
        good_direction="stable",
        typical_range="0.8-1.2",
        caveats=[
            "Requires time tracking, which not all teams do",
            "Should improve over time as team learns",
            "Large variance indicates estimation challenges",
            "Should segment by work type and developer experience"
        ],
        related_metrics=["Cycle Time", "Sprint Commitment Accuracy"]
    ),
]


# ============================================================================
# All Metrics Registry
# ============================================================================

ALL_RD_METRICS = (
    VELOCITY_METRICS +
    QUALITY_METRICS +
    COLLABORATION_METRICS +
    DELIVERY_METRICS +
    PLANNING_METRICS
)

METRICS_BY_NAME = {m.metric_name: m for m in ALL_RD_METRICS}
METRICS_BY_CATEGORY = {
    "velocity": VELOCITY_METRICS,
    "quality": QUALITY_METRICS,
    "collaboration": COLLABORATION_METRICS,
    "delivery": DELIVERY_METRICS,
    "planning": PLANNING_METRICS,
}


# ============================================================================
# Key Dimensions for R&D Analysis
# ============================================================================

RD_DIMENSIONS = {
    "team": "Team or squad name",
    "sprint": "Sprint number or identifier",
    "developer": "Individual developer",
    "work_item_type": "Feature, bug, tech debt, spike, etc.",
    "priority": "P0, P1, P2, P3",
    "component": "Service, module, or component",
    "epic": "Epic or initiative",
    "release": "Release version",
    "quarter": "Fiscal quarter",
}


# ============================================================================
# Common Analysis Patterns
# ============================================================================

class AnalysisPattern(BaseModel):
    """A reusable analysis pattern for R&D efficiency."""
    pattern_name: str
    description: str
    key_metrics: List[str]
    key_dimensions: List[str]
    typical_questions: List[str]
    analysis_steps: List[str]


RD_ANALYSIS_PATTERNS = [
    AnalysisPattern(
        pattern_name="Sprint Retrospective Analysis",
        description="Analyze sprint performance to identify improvement opportunities",
        key_metrics=["Story Points Completed", "Sprint Commitment Accuracy", "Cycle Time", "Defect Rate"],
        key_dimensions=["sprint", "team", "work_item_type"],
        typical_questions=[
            "Did we meet our sprint commitment?",
            "What was our velocity compared to previous sprints?",
            "What types of work took longer than expected?",
            "Were there any quality issues?"
        ],
        analysis_steps=[
            "Compare committed vs completed story points",
            "Calculate velocity trend over last 3-6 sprints",
            "Analyze cycle time distribution by work item type",
            "Check defect rate and escaped defects",
            "Identify blockers or impediments from sprint notes"
        ]
    ),
    AnalysisPattern(
        pattern_name="Code Quality Trend Analysis",
        description="Track code quality metrics over time to identify degradation",
        key_metrics=["Defect Rate", "Test Coverage", "Code Churn", "Escaped Defects"],
        key_dimensions=["sprint", "component", "developer"],
        typical_questions=[
            "Is our defect rate increasing?",
            "Which components have the most quality issues?",
            "Is test coverage improving?",
            "Are we catching defects before production?"
        ],
        analysis_steps=[
            "Plot defect rate trend over last 6 months",
            "Identify components with highest defect density",
            "Compare test coverage changes over time",
            "Analyze escaped defects by root cause",
            "Correlate code churn with defect rate"
        ]
    ),
    AnalysisPattern(
        pattern_name="Delivery Predictability Analysis",
        description="Assess how predictably the team delivers work",
        key_metrics=["Cycle Time", "Lead Time", "Sprint Commitment Accuracy", "Estimation Accuracy"],
        key_dimensions=["sprint", "work_item_type", "team"],
        typical_questions=[
            "How predictable is our delivery?",
            "What causes delays?",
            "Are we improving our estimation accuracy?",
            "Which types of work are most unpredictable?"
        ],
        analysis_steps=[
            "Calculate cycle time variance over time",
            "Analyze sprint commitment accuracy trend",
            "Identify work items with longest cycle time",
            "Compare estimated vs actual time by work type",
            "Identify common blockers or dependencies"
        ]
    ),
    AnalysisPattern(
        pattern_name="Team Collaboration Health",
        description="Assess collaboration patterns and identify silos",
        key_metrics=["PR Review Time", "PR Size", "Knowledge Silos", "Code Ownership Distribution"],
        key_dimensions=["team", "developer", "component"],
        typical_questions=[
            "Are PRs being reviewed promptly?",
            "Are PRs too large?",
            "Do we have knowledge silos?",
            "Is code ownership well-distributed?"
        ],
        analysis_steps=[
            "Calculate median PR review time by team",
            "Analyze PR size distribution",
            "Identify files touched by only one developer",
            "Calculate bus factor for critical components",
            "Analyze review participation patterns"
        ]
    ),
]


# ============================================================================
# Helper Functions
# ============================================================================

def get_metric_definition(metric_name: str) -> Optional[RDMetricDefinition]:
    """Get metric definition by name."""
    return METRICS_BY_NAME.get(metric_name)


def get_metrics_by_category(category: str) -> List[RDMetricDefinition]:
    """Get all metrics in a category."""
    return METRICS_BY_CATEGORY.get(category, [])


def suggest_related_metrics(metric_name: str) -> List[str]:
    """Suggest related metrics to analyze together."""
    metric = get_metric_definition(metric_name)
    if metric:
        return metric.related_metrics
    return []


def get_analysis_pattern(pattern_name: str) -> Optional[AnalysisPattern]:
    """Get analysis pattern by name."""
    for pattern in RD_ANALYSIS_PATTERNS:
        if pattern.pattern_name == pattern_name:
            return pattern
    return None
