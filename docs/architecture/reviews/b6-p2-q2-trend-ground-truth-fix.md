# B6 P2-1 Fix — Q2 Trend Ground Truth

**Date:** 2026-11-18

## Root Cause (see b6-post-b11-p2-root-cause-analysis.md)

Hand-authored golden placeholders (`monthly_revenue = 0`) for the two Q2
2026 trend cases; the frozen dataset itself is correct.

## Change

`tests/evaluation/golden_cases/monthly_trend.json` — corrected 6 expectations
across `trend_analysis_005` and `trend_analysis_006`:

| Case | Period | Department | Old | New (from dataset) |
|---|---|---|---|---|
| trend_analysis_005 | 2026-04 | 杭州开发一部 | 0 | 632016 |
| trend_analysis_005 | 2026-05 | 杭州开发一部 | 0 | 665280 |
| trend_analysis_005 | 2026-06 | 杭州开发一部 | 0 | 632940 |
| trend_analysis_006 | 2026-04 | 杭州开发二部 | 0 | 721116 |
| trend_analysis_006 | 2026-05 | 杭州开发二部 | 0 | 757680 |
| trend_analysis_006 | 2026-06 | 杭州开发二部 | 0 | 719565 |

Dataset unchanged (sha `f7ee4d68…` under canonical hashing).

## Ground-Truth Basis

`monthly_revenue(department, period) = revenue_actual` of the unique dataset
row for that (department, period) pair; each pair resolves to exactly one
row (verified by the fix script assertion).

## Anti-Regression Guard

`tests/test_b6_golden_ground_truth.py::TestQ2TrendGroundTruth` — asserts the
corrected values against the dataset and recomputes via the benchmark runner;
`TestGoldenGroundTruthConsistency` recomputes every scored golden expectation
across all 50 cases.
