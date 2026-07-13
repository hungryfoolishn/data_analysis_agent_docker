# Week 4 Implementation: R&D Efficiency Domain Specialization

**Date**: 2026-04-09  
**Target User**: Data Analyst  
**Vertical Industry**: R&D Management Efficiency

## Overview

Week 4 focuses on specializing the data analysis agent for R&D management efficiency analysis. This includes:
- Domain knowledge integration (R&D metrics, best practices, anti-patterns)
- Metric calculation and validation library
- Domain-specific validators
- Reusable analysis templates
- Agent prompt enhancements

## Implementation Summary

### 1. Domain Knowledge Module (`rd_efficiency_domain.py`)

**Purpose**: Define comprehensive R&D efficiency domain knowledge including metrics, dimensions, and analysis patterns.

**Key Components**:

#### Metric Definitions (20+ metrics across 5 categories)

```python
# Velocity Metrics
- Story Points Completed: Sum of story points per sprint
- Throughput: Items completed per time period
- Code Churn: Lines changed per period

# Quality Metrics
- Defect Rate: Bugs per feature delivered
- Test Coverage: Percentage of code covered by tests
- Escaped Defects: Defects found in production

# Collaboration Metrics
- PR Review Time: Time to first review
- PR Size: Lines changed per PR
- Knowledge Silos: Files touched by only one developer

# Delivery Metrics
- Cycle Time: Time from start to completion
- Lead Time: Time from creation to completion
- Deployment Frequency: Deployments per week
- Change Failure Rate: Failed deployments percentage

# Planning Metrics
- Sprint Commitment Accuracy: Committed vs completed work
- Estimation Accuracy: Actual vs estimated time
```

Each metric includes:
- Definition and calculation method
- Unit and good direction (higher/lower/stable)
- Typical ranges
- Caveats and common pitfalls
- Related metrics to analyze together

#### Analysis Patterns (4 reusable patterns)

```python
1. Sprint Retrospective Analysis
   - Compare committed vs completed work
   - Analyze velocity trend
   - Identify blockers

2. Code Quality Trend Analysis
   - Track defect rate over time
   - Identify high-risk components
   - Correlate with test coverage

3. Delivery Predictability Analysis
   - Assess cycle time variance
   - Analyze estimation accuracy
   - Identify common blockers

4. Team Collaboration Health
   - PR review time analysis
   - Knowledge silo detection
   - Code ownership distribution
```

### 2. Metric Library (`rd_metric_library.py`)

**Purpose**: Provide calculation functions, validation logic, and interpretation helpers for R&D metrics.

**Key Functions**:

#### Calculation Functions
```python
calculate_velocity(df, sprint_col, points_col) -> DataFrame
calculate_throughput(df, period_col, freq) -> DataFrame
calculate_cycle_time(df, start_col, end_col) -> Series
calculate_defect_rate(features_df, bugs_df, period_col) -> DataFrame
calculate_pr_review_time(df, created_col, reviewed_col) -> Series
calculate_deployment_frequency(df, date_col, freq) -> DataFrame
```

#### Validation Functions
```python
validate_velocity_calculation(df, velocity_value) -> (bool, List[str])
validate_cycle_time_calculation(cycle_times) -> (bool, List[str])
validate_defect_rate_calculation(feature_count, bug_count, rate) -> (bool, List[str])
```

Validators check for:
- Missing or invalid data
- Unusually high/low values
- Negative values (data errors)
- Zero denominators

#### Interpretation Helpers
```python
interpret_velocity_trend(velocities) -> str
# "Velocity is increasing (+15.3%) - now 42.5 points"

interpret_cycle_time(median_days, p90_days) -> str
# "Cycle time is fast (median 5.2 days) with good consistency (P90 7.8 days)"

interpret_defect_rate(rate) -> str
# "Defect rate of 0.25 bugs/feature is good"
```

#### Data Quality Checks
```python
check_required_columns(df, required_cols) -> (bool, List[str])
check_date_range(df, date_col, expected_days) -> (bool, str)
check_duplicate_records(df, key_cols) -> (bool, int)
check_null_values(df, critical_cols) -> Dict[str, int]
```

### 3. Domain Validators (`rd_validators.py`)

**Purpose**: Validate metrics, findings, and analysis against R&D best practices.

**Key Validators**:

#### Metric Definition Validation
```python
validate_rd_metric_definition(metric_def) -> (bool, List[str])
```
- Checks if metric matches standard R&D definitions
- Validates calculation methods
- Warns about common mistakes

#### Finding Validation
```python
validate_rd_finding(finding) -> (bool, List[str])
```

Detects anti-patterns:
- Comparing velocity across teams (velocity is team-specific)
- Using test coverage alone as quality metric
- Blaming individuals (focus on process)
- Ignoring work item type/size in cycle time analysis

#### Analysis Completeness Validation
```python
validate_rd_analysis_completeness(findings, metrics) -> (bool, List[str])
```
- Checks for category balance (velocity, quality, delivery, collaboration)
- Suggests missing analyses
- Recommends related metrics

#### Data Quality Validators
```python
validate_sprint_data(df) -> (bool, List[str])
validate_pr_data(df) -> (bool, List[str])
validate_deployment_data(df) -> (bool, List[str])
```

Check for:
- Zero/negative story points
- Very large PRs (>1000 lines)
- High change failure rate (>30%)
- Date inconsistencies

### 4. Analysis Templates (`rd_templates.py`)

**Purpose**: Provide reusable templates for common R&D analysis scenarios.

**Templates Implemented** (7 templates):

```python
1. Sprint Retrospective Analysis (RD-T001)
   - Difficulty: Easy
   - Key metrics: Story Points, Commitment Accuracy, Cycle Time
   - Analysis steps: Compare committed vs completed, velocity trend, blockers

2. Velocity Trend Analysis (RD-T002)
   - Difficulty: Medium
   - Key metrics: Velocity, Velocity Stability
   - Analysis steps: Plot trend, calculate moving average, identify outliers

3. Code Quality Trend Analysis (RD-T003)
   - Difficulty: Medium
   - Key metrics: Defect Rate, Escaped Defects, Test Coverage
   - Analysis steps: Track defect rate, identify high-risk components

4. Escaped Defects Root Cause Analysis (RD-T004)
   - Difficulty: Hard
   - Key metrics: Escaped Defects, Mean Time to Detect
   - Analysis steps: Root cause analysis, identify process gaps

5. Cycle Time Analysis (RD-T005)
   - Difficulty: Medium
   - Key metrics: Cycle Time, Lead Time, Throughput
   - Analysis steps: Calculate median/P90, identify bottlenecks

6. Deployment Frequency and Stability (RD-T006)
   - Difficulty: Medium
   - Key metrics: Deployment Frequency, Change Failure Rate
   - Analysis steps: DORA metrics, compare to benchmarks

7. Pull Request Review Analysis (RD-T007)
   - Difficulty: Medium
   - Key metrics: PR Review Time, PR Size
   - Analysis steps: Review time analysis, size distribution
```

Each template includes:
- Required and optional data columns
- Key metrics to calculate
- Step-by-step analysis instructions
- Expected findings
- Report structure
- Example questions
- Caveats and warnings

**Template Matching**:
```python
suggest_template(user_question, available_columns) -> Optional[AnalysisTemplate]
```
Automatically suggests appropriate template based on:
- Keywords in user question
- Available data columns

### 5. Agent Integration

**Prompt Enhancements**:

Added R&D domain expertise section to system prompt:
```
## Domain expertise: R&D Management Efficiency
You have deep knowledge of R&D efficiency metrics and best practices:
- Velocity metrics: Story points, throughput, code churn
- Quality metrics: Defect rate, test coverage, escaped defects
- Collaboration metrics: PR review time, PR size, knowledge silos
- Delivery metrics: Cycle time, lead time, deployment frequency
- Planning metrics: Sprint commitment accuracy, estimation accuracy

When analyzing R&D data, you automatically:
- Recognize standard metrics and apply industry best practices
- Validate metric definitions against R&D standards
- Identify common anti-patterns
- Suggest related metrics
- Apply appropriate aggregation methods
```

Added R&D analysis guidelines:
```
## R&D Efficiency Analysis Guidelines

**Metric validation**:
- Check if metrics match standard definitions
- Validate calculation methods
- Apply appropriate aggregation (median for cycle time, sum for velocity)

**Common anti-patterns to avoid**:
- Comparing velocity across teams
- Using test coverage alone as quality metric
- Blaming individuals
- Ignoring work item type/size in cycle time

**Template matching**:
- Check if analysis matches standard template
- Follow template analysis steps

**Domain-specific validators**:
- Use rd_validators for metric definitions and findings
- Use rd_metric_library for calculations and interpretations
```

**Tool Integration**:

1. **eda_profile**: Detects R&D data patterns and suggests templates
```python
template = suggest_template("", available_columns)
if template:
    session.ns["suggested_template"] = template
    # Add template hints to EDA output
```

2. **record_finding**: Validates findings against R&D best practices
```python
is_valid, rd_errors = validate_rd_finding(finding)
if not is_valid:
    return "[WARNING] Finding has R&D domain issues: ..."
```

3. **declare_metric**: Validates metric definitions and suggests related metrics
```python
is_valid, rd_errors = validate_rd_metric_definition(metric_def)
related = suggest_related_metrics(metric_name)
if related:
    return f"Related metrics to consider: {', '.join(related)}"
```

4. **finish_report**: Validates analysis completeness
```python
is_complete, rd_warnings = validate_rd_analysis_completeness(
    session.findings, session.metric_definitions
)
```

## Usage Examples

### Example 1: Sprint Retrospective

**User Question**: "分析 Sprint 15 的表现"

**Agent Behavior**:
1. `eda_profile` detects sprint data (columns: sprint, story_points, status)
2. Suggests "Sprint Retrospective Analysis" template
3. Follows template steps:
   - Calculate velocity: 45 points completed vs 50 committed
   - Compare to previous sprints: velocity stable around 42-48 points
   - Analyze cycle time by work item type
4. `declare_metric`: "Story Points Completed" with standard definition
5. `record_finding`: "Sprint 15 commitment accuracy was 90% (45/50 points)"
   - Validator checks: No anti-patterns detected
6. `finish_report`: Includes all template-recommended sections

### Example 2: Quality Analysis

**User Question**: "代码质量趋势如何？"

**Agent Behavior**:
1. `eda_profile` detects quality data (columns: bug_id, severity, found_in_stage)
2. Suggests "Code Quality Trend Analysis" template
3. `declare_metric`: "Defect Rate" with calculation method
   - Validator warns: "Should correlate with test coverage"
4. Calculates defect rate: 0.28 bugs/feature
5. `record_finding`: "Defect rate is 0.28 bugs/feature (good)"
   - Uses `interpret_defect_rate()` for interpretation
6. Validator suggests: "No test coverage analysis - consider adding"

### Example 3: Anti-Pattern Detection

**User Question**: "Team A 的 velocity 比 Team B 高 20%"

**Agent Behavior**:
1. `record_finding` called with statement about velocity comparison
2. `validate_rd_finding()` detects anti-pattern:
   - "Comparing velocity across teams is an anti-pattern"
   - "Story points are relative and team-specific"
3. Returns warning to agent
4. Agent revises finding to focus on within-team trends

## Benefits

1. **Domain Expertise**: Agent has deep R&D efficiency knowledge
2. **Best Practices**: Automatically applies industry standards
3. **Anti-Pattern Detection**: Prevents common mistakes
4. **Guided Analysis**: Templates provide structure for common scenarios
5. **Validation**: Ensures metrics and findings follow R&D conventions
6. **Interpretation**: Human-readable explanations of metrics
7. **Completeness**: Checks for balanced analysis across categories

## Testing

Create test file `tests/test_week4_rd_domain.py`:

```python
def test_metric_definition_validation():
    # Test standard metric validation
    metric = MetricDefinition(
        metric_name="Story Points Completed",
        definition_text="Sum of story points for completed items"
    )
    is_valid, errors = validate_rd_metric_definition(metric)
    assert is_valid

def test_anti_pattern_detection():
    # Test velocity comparison anti-pattern
    finding = Finding(
        finding_id="F001",
        statement="Team A velocity is 20% higher than Team B",
        evidence=[EvidenceItem(evidence_text="Team A: 50 points, Team B: 40 points")],
        evidence_level="B"
    )
    is_valid, errors = validate_rd_finding(finding)
    assert not is_valid
    assert any("velocity across teams" in err for err in errors)

def test_template_matching():
    # Test template suggestion
    columns = ["sprint", "story_points", "status", "started_date", "completed_date"]
    template = suggest_template("", columns)
    assert template is not None
    assert template.template_name == "Sprint Retrospective Analysis"

def test_metric_calculation():
    # Test velocity calculation
    df = pd.DataFrame({
        "sprint": ["S1", "S1", "S2", "S2"],
        "story_points": [5, 8, 3, 10]
    })
    velocity = calculate_velocity(df, "sprint", "story_points")
    assert velocity.loc[velocity["sprint"] == "S1", "velocity"].values[0] == 13
    assert velocity.loc[velocity["sprint"] == "S2", "velocity"].values[0] == 13

def test_interpretation_helpers():
    # Test velocity trend interpretation
    velocities = [40, 42, 45, 48, 50]
    interpretation = interpret_velocity_trend(velocities)
    assert "increasing" in interpretation.lower()
```

## Files Created

1. `langgraph_langchain/rd_efficiency_domain.py` (450 lines)
   - 20+ metric definitions
   - 4 analysis patterns
   - Helper functions

2. `langgraph_langchain/rd_metric_library.py` (550 lines)
   - 8 calculation functions
   - 4 validation functions
   - 4 interpretation helpers
   - 4 data quality checks

3. `langgraph_langchain/rd_validators.py` (450 lines)
   - Metric definition validator
   - Finding validator (anti-patterns)
   - Analysis completeness validator
   - Data quality validators (sprint, PR, deployment)

4. `langgraph_langchain/rd_templates.py` (500 lines)
   - 7 analysis templates
   - Template matching logic
   - Template registry

5. `langgraph_langchain/langgraph_agent.py` (modified)
   - Added imports for R&D modules
   - Enhanced system prompt with R&D expertise
   - Integrated validators in tools
   - Added template detection in eda_profile

## Next Steps

1. **Testing**: Create comprehensive test suite for Week 4 functionality
2. **Documentation**: Add user guide for R&D analysis features
3. **Benchmark**: Create R&D-specific benchmark dataset
4. **Templates**: Add more templates for advanced scenarios
5. **Metrics**: Expand metric library with more DORA and SPACE metrics

## Conclusion

Week 4 successfully specializes the data analysis agent for R&D management efficiency analysis. The agent now has:
- Deep domain knowledge of R&D metrics
- Automatic validation against best practices
- Anti-pattern detection
- Guided analysis through templates
- Human-readable interpretations

This makes the agent significantly more valuable for data analysts working in R&D management contexts.
