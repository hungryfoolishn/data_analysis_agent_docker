# R3 Evaluation & Gate

Executed on docker-host, conda env `analysis`, worktree
`/python/pragrams/data_analysis_agent_convergence`, branch
`codex/architecture-convergence-v1.1-e2e`.

## Evaluation framework baseline (audited, not rewritten)

| Component | Entry | Status |
| --- | --- | --- |
| Golden cases | `tests/evaluation/golden_cases/` — 50 cases across 7 files (monthly_trend 6, q2_1_metrics 10, multi_metric 6, anomaly_detection 6, period_comparison 8, department_compare 8, comprehensive_analysis 6) | VERIFIED |
| Case loader | `runtime/evaluation/golden_loader.py` — duplicate-id rejection, dataset path confinement to evaluation root, content-hash check | VERIFIED |
| Scoring | `runtime/evaluation/runner.py` — weighted: execution 0.10, verification 0.10, numeric 0.30, evidence 0.15, finding 0.15, report 0.20; pass requires correctness-critical components | VERIFIED |
| Regression tests | evaluation runner/deterministic + runtime golden E2E + B6 ground truth | 37 passed this stage |

## Test evidence (executed this stage)

| Command | Result |
| --- | --- |
| `python -m pytest tests/test_r25_legacy_parity.py tests/test_r3_unified_orchestration_budget.py -q` | 8 passed |
| Dispatcher-affected suites (planner v2 contract, executors, output bindings, runner hooks, graph, B8 integration, V1/V2 parity) | 74 passed |
| Intelligence/evaluation/planner suites | 37 passed |
| `python -m pytest tests/ -q` (full regression) | **1969 passed, 2 failed, 13 skipped**, 16m34s |

The 2 failures are the known frozen Dashboard failures, identical node ids to every
previous stage baseline and explicitly out of scope:

- `tests/test_dashboard.py::test_individual_metrics_endpoints`
- `tests/test_dashboard_display_format` — exact id: `tests/test_dashboard.py::test_dashboard_display_format`

Regression delta from Stage E: 1965P/2F/13S → 1969P/2F/13S (+4 new R3 budget tests,
zero new failures, zero flakes).

## Gate

| Gate | Result | Evidence |
| --- | --- | --- |
| Scope compliance | PASS | Only unified-budget convergence + audit/evolution/evaluation docs; no frozen contract touched; `RUNTIME_V2_ENABLED` default unchanged |
| Code review — correctness | PASS | Budget consumed before dispatch; exhaustion → recorded failed result; blocked calls do not consume budget |
| Code review — security | PASS | Surface restriction governs react/python too; no data/evidence boundary change |
| Regression | PASS | Full suite unchanged except new passes |
| Intelligence audit | PASS | 17 capabilities audited with code anchors; gaps explicitly deferred with safe defaults |
| Golden task regression | PASS | 50 golden cases + loader/scoring tests pass |

## V1.1 §9.4 exit criteria checklist

- [x] Verifiable planning — deterministic planner + full plan validation + LLM re-validation (existing, audited)
- [x] Bounded dynamic exploration — unified fail-closed budget across structured/react/python (implemented in R3)
- [x] Traceable tool orchestration — single dispatch point with recorded ExecutionResult per call
- [x] Verifiable evidence — EvidenceCollector + VerificationPolicy per result (existing, untouched)
- [x] Detectable analysis errors — golden numeric scoring + verification policy (existing, audited)
- [x] Recoverable execution flow — per-task failure records, strict-mode outcomes, cancellation (existing; retry budget deferred with safe default)
- [x] Evaluatable intelligence metrics — evaluation framework with weighted scores (audited)
- [x] Golden task regression — 50-case suite green

Deferred items (adaptive re-planning, retry budget, context size limits, LLM planner benchmark)
are documented in `02-intelligence-evolution.md` with the safe defaults that cover them now.
