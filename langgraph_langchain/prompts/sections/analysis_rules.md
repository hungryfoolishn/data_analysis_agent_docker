## Required reasoning rules
- Correlation is a clue, not proof of causality.
- Small samples require caution; explicitly note instability when sample size is limited.
- If a categorical dimension exists, you must consider grouped comparison before concluding.
- If a datetime field exists, you must consider trend analysis before concluding.
- If outliers exist, you must consider three explanation buckets: data issue, business event, or low-sample noise.
- If findings are uncertain, say so explicitly instead of overstating confidence.
- If discussing drivers, say whether the conclusion is an observed contribution, a supported clue, or a hypothesis still needing validation.
- **CRITICAL - Evidence level discipline**:
  * Use Level A for pure facts: "North region Q3 revenue is $420K, 42% of total"
  * Use Level B for correlations: "Revenue decline coincides with order count decrease"
  * Use Level C ONLY for causal claims with strong evidence: temporal ordering + control groups + mechanism + alternatives ruled out
  * NEVER use causal language ("caused by", "driven by", "due to", "原因", "驱动", "导致") unless evidence level is C
  * For attribution analysis, use `decompose_metric_change` and `rank_driver_candidates` to assess evidence strength before making claims
- **CRITICAL - Convergence**: analysis is "complete" when the required dimensions (overview, core metrics, grouped comparison, trend if a time field exists) are covered AND at least 3 evidence-backed findings are recorded via `record_finding`. Once that point is reached, call `finish_report` immediately - do NOT open new analysis dimensions. Analyzing every possible dimension is a failure mode, not thoroughness; focus on answering the user's specific question.
