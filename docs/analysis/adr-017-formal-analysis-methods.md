# ADR-017: Second Group of Formal Analysis Methods

## Decision

Add deterministic, domain-neutral methods for distribution profiling, association,
period comparison, explicit ratios, funnel, retention, group differences, concentration, and sensitivity checks. Each method has a Pydantic result,
structured failure conditions, disclosure text, and a CSV artifact through the
existing execution recorder.

## Methods

- profile_distribution: quantiles, spread, skewness, IQR fences, missingness,
  and a descriptive long-tail signal.
- analyze_correlation: Pearson or Spearman association with pairwise-complete
  sample size and an explicit non-causal disclosure.
- compare_periods: two declared or latest adjacent periods with aggregation,
  comparability, row counts, and denominator-aware percent change.
- calculate_ratio: explicit numerator/denominator aggregation and zero-denominator
  handling.
- analyze_funnel and analyze_retention: entity de-duplication, cohorts, and
  retention disclosures.
- test_group_difference, analyze_concentration, and run_sensitivity_check:
  descriptive effect, concentration, and aggregation-sensitivity checks.

## Boundaries

These methods do not infer causality, choose business fields, or silently repair
missing denominators. These methods remain descriptive; they do not infer causality or silently repair data definitions.
