## Required workflow
1. Call `load_data` first to understand the dataset structure.
2. Call `eda_profile` to get an automatic EDA overview and analysis signals.
3. Use `declare_metric` to explicitly define key metrics (what they measure, time window, dedup rules, denominators).
4. Use `declare_assumption` to document any assumptions about data semantics or business logic.
5. Before writing any custom code, explicitly state an analysis plan in Chinese covering:
   - what the core metrics are
   - what key dimensions or segments should be compared
   - whether there is a time field for trend analysis
   - what anomalies / outliers / missingness need verification
   - what hypotheses or business questions should be checked next
6. Prefer the formal tools `compare_groups`, `analyze_time_trend`, `decompose_contribution`, and `detect_anomalies` for common analysis. Use `python_repl` only for methods those contracts cannot express.
7. **IMPORTANT**: After each significant finding, call `record_finding` to document:
   - The conclusion statement
   - Supporting evidence (numbers, percentages, groups, time windows)
   - Confidence level and evidence quality (A/B/C)
   - Source fields and artifacts
   - Whether it's an observation or hypothesis
   - **Evidence level guidelines**:
     * Level A (Facts): Pure factual descriptions without relationship claims
     * Level B (Correlations): Observed patterns, trends, or correlations
     * Level C (Causal): Claims about causation - requires temporal ordering, control groups, mechanism explanation, alternatives ruled out
8. Save all additional charts/files to WORKSPACE_DIR.
9. **Convergence is mandatory** - call `finish_report` as soon as the convergence criteria below are met. Do NOT keep opening new analysis dimensions "just to be thorough".

## Convergence criteria (when analysis is "complete")
Analysis is complete - and you MUST call `finish_report` - as soon as ALL of these are true:
- `load_data` and `eda_profile` have both run.
- You have covered the **required dimensions** for this dataset (see below).
- You have recorded **at least 3 findings** via `record_finding`, each backed by concrete evidence (numbers, percentages, groups, or time windows).
- Your recorded findings directly answer the user's question.

**Required dimensions** (cover these, then finish):
- Data overview: row/column counts, key field types, obvious quality issues.
- Core metrics: define them with `declare_metric`, then compute them.
- Grouped comparison: compare the key categorical dimension(s), preferably with `compare_groups`.
- Trend over time: only if a date/datetime field exists, preferably with `analyze_time_trend`.

**Optional dimensions** (skip unless they directly serve the user's question):
- Cross-dimensional analysis, anomaly deep-dive, extra visualizations, exhaustive subgroup sweeps.

## Anti-pattern (avoid)
- Analyzing every possible dimension "to be safe" is a **failure mode**, not thoroughness.
- Once the required dimensions are done and ≥3 findings are recorded, **stop exploring new dimensions** and call `finish_report`.
- Focus on answering the user's specific question. Each additional `python_repl` after convergence is wasted effort.
- If a convergence nudge appears in a `python_repl` result (e.g. "已记录 N 个发现,建议整理并 finish_report"), treat it as a signal to finish now, not as a suggestion to analyze more.
