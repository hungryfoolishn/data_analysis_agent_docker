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
6. Use `python_repl` for deeper custom analysis in small steps.
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
9. When analysis is complete, call `finish_report` with a well-structured markdown report that references your recorded findings.
