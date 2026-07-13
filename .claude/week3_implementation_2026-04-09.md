# Week 3 Implementation: Reviewable Governance

**Date**: 2026-04-09  
**Status**: ✅ Completed

## Overview

Week 3 focuses on making analysis conclusions reviewable and traceable. The core principle is: **every conclusion must be backed by evidence, and the evidence level must match the strength of the claim**.

## Key Changes

### 1. Enhanced Evidence-Conclusion Binding

**File**: `langgraph_langchain/schemas.py`

Enhanced the `Finding` and `EvidenceItem` structures to capture more detailed evidence:

```python
class EvidenceItem(BaseModel):
    evidence_text: str
    source_fields: List[str]
    source_artifacts: List[str]
    time_window: Optional[str]
    group_dimension: Optional[str]
    filters: List[str]
    stats: Optional[dict]  # NEW: Key statistics like {"north_revenue": 420000, "total_revenue": 1000000}
    calculation_method: Optional[str]  # NEW: How the finding was calculated

class Finding(BaseModel):
    finding_id: str  # NEW: Unique ID like "F001"
    statement: str
    evidence: List[EvidenceItem]
    assumptions: List[AnalysisAssumption]
    metric_definitions: List[MetricDefinition]
    confidence_level: Literal["low", "medium", "high"]
    evidence_level: Literal["A", "B", "C"]  # NEW: Required, default "B"
    hypothesis_flag: bool
    category: Optional[str]  # NEW: e.g., "trend", "anomaly", "comparison"
```

**Key improvements**:
- Every finding now has a unique ID (F001, F002, etc.)
- Evidence can include raw statistics for full traceability
- Calculation method documents how the finding was derived
- Evidence level is now required (not optional)

### 2. Evidence Level Classification System

**File**: `langgraph_langchain/evidence_validator.py` (NEW)

Implemented a three-tier evidence classification system:

**Level A - Facts**: Pure factual descriptions without relationship claims
- Example: "North region Q3 revenue is $420K, accounting for 42% of total"
- No causal or correlation language
- Just stating what the data shows

**Level B - Correlations**: Observed patterns, trends, or correlations
- Example: "Revenue decline coincides with order count decrease in the same period"
- Shows relationships but doesn't claim causation
- Most findings should be Level B

**Level C - Causal Claims**: Claims about causation (requires strong evidence)
- Example: "Price increase caused 15% drop in conversion rate"
- Requirements:
  - Temporal ordering (cause before effect)
  - Control groups or comparison
  - Mechanism explanation
  - Alternative explanations ruled out
  - At least 2 pieces of evidence

**Validation logic**:
```python
def validate_evidence_level(finding: Finding) -> Tuple[bool, str]:
    """
    Validates that:
    - Causal language ("caused by", "driven by", "原因", "驱动") requires Level C
    - Correlation language requires at least Level B
    - Level A findings don't make relationship claims
    """
```

### 3. Recommendation-Evidence Binding

**File**: `langgraph_langchain/recommendation_validator.py` (NEW)

Implemented a system to ensure recommendations match evidence strength:

**Three recommendation types**:

1. **Immediate Action** (requires strong evidence)
   - High confidence + Level B/C evidence + multiple evidence items
   - Can use: "should", "must", "prioritize", "立即", "全面推广"
   - Example: "Should increase investment in North region"

2. **Validation** (medium evidence)
   - Medium/high confidence + Level B/C + at least 1 evidence item
   - Should use: "建议验证", "suggest testing", "进一步分析"
   - Example: "Suggest validating the hypothesis with A/B test"

3. **Observation** (weak evidence)
   - Low confidence or Level A evidence
   - Should use: "持续观察", "continue monitoring", "建立预警"
   - Example: "Continue monitoring this metric for next quarter"

**Validation logic**:
```python
def validate_recommendations_against_findings(
    report_markdown: str,
    findings: List[Finding]
) -> List[str]:
    """
    Checks if strong action recommendations are backed by
    sufficient evidence (immediate action findings).
    """
```

### 4. Updated Agent Prompt

**File**: `langgraph_langchain/langgraph_agent.py`

Updated the system prompt to enforce evidence level discipline:

```python
## Required workflow
7. **IMPORTANT**: After each significant finding, call `record_finding` to document:
   - **Evidence level guidelines**:
     * Level A (Facts): Pure factual descriptions without relationship claims
     * Level B (Correlations): Observed patterns, trends, or correlations
     * Level C (Causal): Claims about causation - requires temporal ordering, 
       control groups, mechanism explanation, alternatives ruled out

## Required reasoning rules
- **CRITICAL - Evidence level discipline**:
  * Use Level A for pure facts: "North region Q3 revenue is $420K, 42% of total"
  * Use Level B for correlations: "Revenue decline coincides with order count decrease"
  * Use Level C ONLY for causal claims with strong evidence
  * NEVER use causal language unless evidence level is C
  * For attribution analysis, use `decompose_metric_change` and 
    `rank_driver_candidates` to assess evidence strength
```

### 5. Enhanced record_finding Tool

**File**: `langgraph_langchain/langgraph_agent.py`

Updated the `record_finding` tool to support new fields:

```python
@tool
def record_finding(
    statement: str,
    evidence_text: str,
    confidence_level: str = "medium",
    evidence_level: str = "B",  # Default to B
    hypothesis_flag: bool = False,
    category: str = None,  # NEW
    source_fields: List[str] = None,
    source_artifacts: List[str] = None,
    time_window: str = None,
    group_dimension: str = None,
    filters: List[str] = None,
    stats: dict = None,  # NEW
    calculation_method: str = None,  # NEW
) -> str:
    """
    Record a structured finding with evidence during analysis.
    
    EVIDENCE LEVEL GUIDELINES:
    - Level A (Facts): Pure factual descriptions
    - Level B (Correlations): Observed patterns, trends
    - Level C (Causal): Claims about causation (requires strong evidence)
    """
```

### 6. Integrated Validators in finish_report

**File**: `langgraph_langchain/langgraph_agent.py`

Added validation checks in the `finish_report` tool:

```python
# Week 3: Validate evidence levels for all findings
from langgraph_langchain.evidence_validator import validate_findings_evidence_levels
from langgraph_langchain.recommendation_validator import validate_recommendations_against_findings

evidence_errors = validate_findings_evidence_levels(session.findings)
if evidence_errors:
    for error in evidence_errors:
        issues.append(f"evidence_level_violation: {error}")

# Week 3: Validate recommendations are backed by sufficient evidence
recommendation_errors = validate_recommendations_against_findings(markdown, session.findings)
if recommendation_errors:
    for error in recommendation_errors:
        issues.append(f"recommendation_evidence_mismatch: {error}")
```

## Usage Examples

### Example 1: Recording a Level A Finding (Pure Fact)

```python
record_finding(
    statement="North region Q3 revenue is $420,000, accounting for 42% of total revenue",
    evidence_text="Calculated from revenue field grouped by region for Q3 2025",
    confidence_level="high",
    evidence_level="A",  # Pure fact
    category="comparison",
    source_fields=["region", "revenue", "date"],
    time_window="2025-Q3",
    stats={"north_revenue": 420000, "total_revenue": 1000000},
    calculation_method="SUM(revenue) WHERE region='North' AND date BETWEEN '2025-07-01' AND '2025-09-30'"
)
```

### Example 2: Recording a Level B Finding (Correlation)

```python
record_finding(
    statement="Revenue decline coincides with order count decrease in the same period",
    evidence_text="Revenue dropped 15% while order count dropped 12% in Q3 vs Q2",
    confidence_level="medium",
    evidence_level="B",  # Correlation
    category="trend",
    source_fields=["revenue", "order_count", "date"],
    time_window="Q3 2025 vs Q2 2025",
    stats={"q3_revenue": 850000, "q2_revenue": 1000000, "q3_orders": 880, "q2_orders": 1000}
)
```

### Example 3: Recording a Level C Finding (Causal)

```python
record_finding(
    statement="Price increase caused 15% drop in conversion rate",
    evidence_text="After price increase on 2025-07-15, conversion rate dropped from 5% to 4.25%. Control group (no price change) maintained 5% conversion. Drop persisted across all customer segments.",
    confidence_level="high",
    evidence_level="C",  # Causal claim
    category="attribution",
    source_fields=["price", "conversion_rate", "date", "customer_segment"],
    time_window="2025-07-15 to 2025-09-30",
    stats={
        "pre_conversion": 0.05,
        "post_conversion": 0.0425,
        "control_conversion": 0.05,
        "drop_percentage": 0.15
    },
    calculation_method="A/B test with control group, stratified by customer segment"
)
```

## Validation Examples

### Valid: Level A with factual language

```python
Finding(
    finding_id="F001",
    statement="North region accounts for 42% of Q3 revenue",
    evidence_level="A",
    ...
)
# ✅ PASS - Pure fact, no causal language
```

### Invalid: Level A with correlation language

```python
Finding(
    finding_id="F002",
    statement="Revenue is correlated with order count",
    evidence_level="A",
    ...
)
# ❌ FAIL - Correlation language requires Level B
```

### Invalid: Causal language without Level C

```python
Finding(
    finding_id="F003",
    statement="Price increase caused conversion drop",
    evidence_level="B",
    ...
)
# ❌ FAIL - Causal language requires Level C
```

### Valid: Level C with sufficient evidence

```python
Finding(
    finding_id="F004",
    statement="Price increase caused conversion drop",
    evidence_level="C",
    evidence=[evidence1, evidence2],  # At least 2 pieces
    ...
)
# ✅ PASS - Causal claim with Level C and multiple evidence items
```

## Expected Outcomes

1. **Traceability**: Every conclusion can be traced back to specific evidence with statistics and calculation methods

2. **Appropriate Language**: Agent uses appropriate language based on evidence strength:
   - Facts → descriptive language
   - Correlations → "coincides with", "associated with"
   - Causation → only when evidence is strong

3. **Scaled Recommendations**: Recommendations match evidence strength:
   - Strong evidence → immediate action
   - Medium evidence → validation
   - Weak evidence → observation

4. **Validation Feedback**: Agent receives clear feedback when:
   - Using causal language without Level C evidence
   - Making strong recommendations without sufficient evidence
   - Claiming correlations with only Level A evidence

## Testing

To test the Week 3 implementation:

```python
# Test evidence level validation
from langgraph_langchain.evidence_validator import validate_evidence_level
from langgraph_langchain.schemas import Finding, EvidenceItem

# Should fail - causal language without Level C
finding = Finding(
    finding_id="F001",
    statement="Price increase caused conversion drop",
    evidence_level="B",
    evidence=[EvidenceItem(evidence_text="...")],
    ...
)
is_valid, error = validate_evidence_level(finding)
assert not is_valid
assert "causal language" in error

# Test recommendation validation
from langgraph_langchain.recommendation_validator import validate_recommendations_against_findings

report = """
## Recommendations
- Should immediately increase prices in North region
"""
findings = [
    Finding(finding_id="F001", confidence_level="low", evidence_level="A", ...)
]
errors = validate_recommendations_against_findings(report, findings)
assert len(errors) > 0  # Should fail - strong action without strong evidence
```

## Integration with Existing Features

Week 3 builds on Week 1 and Week 2:

- **Week 1 (Credibility)**: Findings structure, metric definitions, assumptions
- **Week 2 (Stability)**: State machine, recovery strategies, failure classification
- **Week 3 (Governance)**: Evidence levels, recommendation binding, validation

All three weeks work together to ensure:
1. Findings are structured and traceable (Week 1)
2. Analysis process is stable and recoverable (Week 2)
3. Conclusions and recommendations match evidence strength (Week 3)

## Summary

Week 3 implements a comprehensive governance system that ensures:
- Every finding has a unique ID and detailed evidence
- Evidence levels (A/B/C) match the language used
- Causal claims require strong evidence (Level C)
- Recommendations are scaled to evidence strength
- Validators provide clear feedback when standards aren't met

This creates a reviewable, traceable analysis system where stakeholders can understand exactly what evidence supports each conclusion.
