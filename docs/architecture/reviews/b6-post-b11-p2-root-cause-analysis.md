# B6 Post-B11 P2 Root Cause Analysis

**Date:** 2026-11-18
**Scope:** two B6 golden data-quality issues surfaced by the B11.6 benchmark.

## 1. Investigation Basis (read-only before any fix)

- Golden dataset: `tests/evaluation/data/sales_performance_2025_2026h1.csv`
  (72 rows, periods 2025-01 … 2026-06, 4 departments).
- Golden JSONs hand-authored in commit `63dfe4c`
  (`feat(runtime-v8.5): add golden evaluation infrastructure`); no
  generator script exists in the repository.
- Dataset hash recorded in the JSONs (`f7ee4d68…`) matches the committed
  dataset under the newline-insensitive `canonical_text_sha256` used by
  `GoldenCaseLoader.validate()` — no dataset version mismatch.
- `revenue_change_pct` / `compare_period` have no implementation anywhere in
  `langgraph_langchain` (planner, tools, ops) prior to B11.6; the metric
  existed only as an expectation name in golden JSONs.
- The Runtime v8.5 design doc (same commit) lists "同比/环比" case types but
  defines no canonical comparison-period contract.

## 2. P2-1 — Q2 Trend Ground Truth (trend_analysis_005/006)

**Symptom:** expected `monthly_revenue = 0` for 2026-04/05/06, while the
frozen dataset contains non-zero revenue rows for those department/period
pairs.

| Candidate root cause | Verdict |
|---|---|
| Dataset error | No — Q2 rows exist in the frozen dataset (`f7ee4d68`, content byte-identical to working copy modulo line endings) |
| Ground-truth error | **Yes** — hand-authored 0 placeholders, no computation basis |
| Time range / parsing / ordering error | No — period strings parse and sort correctly |
| Algorithm error | No — sum of `revenue_actual` per (department, period) row is unambiguous |
| Data version mismatch | No — hash matches under canonical sha256 |
| Metric definition ambiguity | No — monthly_revenue is well-defined |

Correct values (computed independently from the dataset, per department):

- 杭州开发一部 Q2: 2026-04 = 632016, 2026-05 = 665280, 2026-06 = 632940
- 杭州开发二部 Q2: 2026-04 = 721116, 2026-05 = 757680, 2026-06 = 719565

**Classification: ground-truth error → fix the golden expected values,
keep dataset unchanged.**

## 3. P2-2 — comprehensive revenue_change_pct Missing compare_period

**Symptom:** 6 comprehensive expectations (one per case) carry no
`compare_period`; original expected values are inconsistent with any
documented computation.

Investigation of the original values:

| Case | Old value | MoM actual | Match |
|---|---|---|---|
| comprehensive_001 (2026-01) | 0.1 | -0.282544 | ✗ |
| comprehensive_002 (2026-02) | 0.0828 | 0.082840 | ✓ |
| comprehensive_003 (2026-03) | 0.1989 | 0.108907 | ✗ |
| comprehensive_004 (2026-04) | 0.2675 | 0.059743 | ✗ |
| comprehensive_005 (2026-05) | 0.3529 | 0.052632 | ✗ |
| comprehensive_006 (2026-06) | 0.275 | -0.050305 | ✗ |

Only case 002 matches month-over-month; the others match no derivable
formula (MoM target, actual-vs-target checked) — fabricated placeholders.

**Erratum:** the B11.6 report stated "12 metrics"; the correct count is
**6 change_pct expectations** (one per comprehensive case). The original
B11.6 results JSON is preserved untouched; this erratum is recorded here
rather than by rewriting the frozen report.

**Root cause:** no canonical comparison-period contract existed; the metric
was ambiguous by construction.

**Classification: metric-contract gap + ground-truth error → define the
contract explicitly and correct the golden data.**

## 4. Fix Principles Applied

- No expectation was changed merely to make tests pass; every new value is
  independently computed from the frozen dataset and documented.
- No golden case was deleted; no skip was added.
- The computation layer never defaults a missing compare_period: it fails
  explicitly (`UnverifiableMetricError`).
- Anti-regression guard added:
  `tests/test_b6_golden_ground_truth.py` recomputes every scored golden
  expectation from the dataset.
