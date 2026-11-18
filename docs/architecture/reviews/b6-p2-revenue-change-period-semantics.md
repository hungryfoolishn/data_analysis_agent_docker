# B6 P2-2 Fix — revenue_change_pct Comparison Period Semantics

**Date:** 2026-11-18

## Metric Contract (canonical)

```text
revenue_change_pct
  formula:  (current.revenue_actual - compare.revenue_actual)
            / compare.revenue_actual
  current:  expectation.period (row filtered by dimension/group)
  compare:  filters.compare_period   — REQUIRED, no default
  type:     filters.comparison_type  — REQUIRED; golden uses
            "month_over_month" when compare is the previous calendar month;
            "specified_period" when an explicit period pair is given
  unit:     dimensionless ratio (not percent)
```

Rules:

1. Missing `compare_period` must fail explicitly — never default to YoY,
   MoM, or "latest available". Enforcement: `UnverifiableMetricError` in the
   B11.6 benchmark computation; golden validation test requires the field.
2. Missing compare data (unknown period/department) → no value, no guess.
3. Zero baseline (`compare.revenue_actual == 0`) → undefined, returns no
   value.
4. No direct cross-currency or mixed-unit comparison (single CNY dataset).
5. This contract reuses the existing golden filter convention
   (`filters.compare_period`, as already used by `period_comparison.json`
   since B6) — no new parallel period system is created; B10 Period
   Semantics (instant/duration statement periods) is a different domain and
   is not affected.

## Golden Data Change

`tests/evaluation/golden_cases/comprehensive_analysis.json` — all 6
`revenue_change_pct` expectations updated with explicit
`filters.compare_period` (previous calendar month) and
`filters.comparison_type = "month_over_month"`, values recomputed:

| Case | Period | Compare | Old | New |
|---|---|---|---|---|
| comprehensive_001 | 2026-01 | 2025-12 | 0.1 | -0.282544 |
| comprehensive_002 | 2026-02 | 2026-01 | 0.0828 | 0.08284 |
| comprehensive_003 | 2026-03 | 2026-02 | 0.1989 | 0.108907 |
| comprehensive_004 | 2026-04 | 2026-03 | 0.2675 | 0.059743 |
| comprehensive_005 | 2026-05 | 2026-04 | 0.3529 | 0.052632 |
| comprehensive_006 | 2026-06 | 2026-05 | 0.275 | -0.050305 |

Case 002's old value was already correct MoM (only the fields were missing);
the other five were incorrect placeholders.

## Rationale for month_over_month Choice

- The dataset is monthly-grain with contiguous coverage (2025-01 … 2026-06);
  every comprehensive case's previous calendar month exists.
- Case 002's surviving correct value proves the original author intent was
  MoM.
- The question texts are now unambiguous in computation terms because the
  expectation carries an explicit compare_period; no consumer may assume a
  default.

## Anti-Regression Guard

`tests/test_b6_golden_ground_truth.py::TestRevenueChangePeriodContract`:
missing compare_period rejected; specified-period ratio verified; zero
baseline, missing compare data, and missing department all return no value;
all 6 comprehensive expectations assert explicit fields.
