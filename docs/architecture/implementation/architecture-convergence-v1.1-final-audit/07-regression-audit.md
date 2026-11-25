# Regression Audit — Reproduction of 1969 / 2 / 13

## Reproduction 1 — reported HEAD `cfa6c79` (audit worktree, identical environment)

| Item | Value |
| --- | --- |
| Command | `python -m pytest tests/ -q -rs` |
| Environment | docker-host, conda `analysis`, Python 3.10.17 env, audit worktree at `cfa6c79` |
| Duration | 17m23s |
| Result | **1969 passed / 2 failed / 13 skipped**, 4 subtests passed, 117 warnings |
| Verdict | **REPRODUCED — exact match with the report** |

## Reproduction 2 — final audit HEAD `3b13a96` (after remediation F-1)

| Item | Value |
| --- | --- |
| Command | `python -m pytest tests/ -q` |
| Duration | 17m07s |
| Result | **1970 passed / 2 failed / 13 skipped** (+1 concurrency regression test, zero new failures) |
| Verdict | **REPRODUCED — remediation adds exactly its new test** |

## Failure attribution (verified, not assumed)

Both failures reproduce at the pre-V1.1 baseline `cbaecd4` (temporary worktree, then
removed):

- `tests/test_dashboard.py::test_individual_metrics_endpoints` — assertion failure
- `tests/test_dashboard.py::test_dashboard_display_format` — `assert 503 == 200`

Historical evidence: identical node ids and error types in every previous stage report
(Stage A baseline 1944P/2F/13S through Stage F 1969P/2F/13S). The six V1.1 commits did
not touch `tests/test_dashboard.py` or the dashboard implementation. Deterministic
(2.7s re-run, stable). They do not mask other regressions: the full suite executes them
independently. Classification: **pre-existing, unrelated, frozen baseline** — verified.

## Skip audit (13, all environment-gated, none added by V1.1)

| Group | Count | Reason |
| --- | --- | --- |
| External financial smoke | 4 | `RUN_FINANCIAL_EXTERNAL_SMOKE` not enabled |
| Dashboard live-API | 1 | API server not running |
| Macro golden G10/G11 | 4 | insufficient data for template selection |
| Public data adapters | 4 | FRED/BLS/BEA/SEC keys not provided |

No security or core-runtime test is skipped; all 13 skip markers pre-date `cbaecd4`
(diff shows no skip markers added in `cbaecd4..cfa6c79`).

## Test integrity

- No tests deleted/renamed across the six commits (diff-filter=DR empty).
- No skip markers added.
- R2.5 test re-anchoring in `cfa6c79` preserved assertion strength (dispatcher-level
  exhaustion-before-side-effect plus new unified accounting assertions).
- Stage delta traceable: 1944 (A baseline) → 1952 → 1958 → 1958+3 → 1965 → 1969 → 1970
  (audit remediation). Every increment corresponds to named new test files.

**Final regression verdict: REPRODUCED.**
