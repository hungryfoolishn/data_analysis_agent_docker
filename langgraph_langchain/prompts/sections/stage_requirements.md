## Analysis Stage Requirements (ENFORCED)
The analysis follows a strict state machine with stage-based tool restrictions:

**Stage 1: INIT / SCHEMA_UNDERSTANDING**
- Allowed tools: `load_data`, `python_repl` (for basic exploration)
- Purpose: Load data and understand structure
- Must complete before: Any analysis work

**Stage 2: DATA_QUALITY_CHECK**
- Allowed tools: `eda_profile`, `python_repl`, `declare_metric`, `declare_assumption`
- Prerequisites: Must call `load_data` first
- Purpose: Assess data quality and define metrics

**Stage 3: BASIC_EDA**
- Allowed tools: `compare_groups`, `analyze_time_trend`, `detect_anomalies`, `decompose_contribution`, `python_repl`, `declare_metric`, `declare_assumption`
- Prerequisites: Must call `eda_profile` first
- Purpose: Exploratory analysis, distributions, correlations

**Stage 4: DEEP_DIVE**
- Allowed tools: `compare_groups`, `analyze_time_trend`, `detect_anomalies`, `decompose_contribution`, `python_repl`, `record_finding`, `declare_metric`, `declare_assumption`
- Prerequisites: Must complete basic EDA first
- Purpose: Focused analysis, hypothesis testing
- **IMPORTANT**: Must call `record_finding` to document insights

**Stage 5: CONCLUSION_SYNTHESIS**
- Allowed tools: `record_finding`, `decompose_contribution`, `python_repl`
- Prerequisites: Must have recorded findings from deep dive
- Purpose: Organize and synthesize findings

**Stage 6: REPORT_GENERATION**
- Allowed tools: `finish_report`
- Prerequisites: Must have at least 3 recorded findings
- Purpose: Generate final report

**Tool call violations will be rejected with an error message.** Follow the stage progression to ensure analysis quality and consistency.
