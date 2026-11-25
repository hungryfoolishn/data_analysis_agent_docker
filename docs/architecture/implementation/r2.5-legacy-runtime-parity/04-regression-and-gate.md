# R2.5 Regression & Gate

Executed on docker-host, conda env `analysis`, worktree
`/python/pragrams/data_analysis_agent_convergence`, branch
`codex/architecture-convergence-v1.1-e2e` @ `36a3900` plus R2.5 working tree.

## Targeted validation (executed this stage)

| Command | Result |
| --- | --- |
| `python -m pytest tests/test_r25_legacy_parity.py tests/runtime_v2/test_runtime_v1_v2_parity.py -q` | **11 passed**, 14 warnings, 60.4s |

Breakdown: 4 new R2.5 budget-adapter tests + 7 existing real-API V1/V2 parity tests
(real `run_analysis_stream` with a scripted fake chat model; both runtimes executed).

## Full regression (executed this stage)

| Command | Result |
| --- | --- |
| `python -m pytest tests/ -q` | **1965 passed, 2 failed, 13 skipped**, 4 subtests passed, 16m51s |

The 2 failures are the known frozen Dashboard failures, identical node ids to the inherited
baseline, and are explicitly out of scope for convergence work:

- `tests/test_dashboard.py::test_individual_metrics_endpoints`
- `tests/test_dashboard.py::test_dashboard_display_format`

Stage-over-stage regression delta: 1958P/2F/13S (Stage C/D baseline) → 1965P/2F/13S.
+7 tests (4 R2.5 budget tests + 3 R2 capability-inventory tests), zero new failures,
zero flakes introduced.

## Gate

| Gate | Result | Evidence |
| --- | --- | --- |
| Scope compliance | PASS | Only: new `runtime/capability_adapter.py`, `StructuredTaskExecutor` budget param, budget wiring in `_run_runtime_v2_graph_stream`, new tests, R2.5 docs |
| Code review — correctness | PASS | Budget consumed before tool resolution; exhaustion raises before side effect (tested) |
| Code review — security | PASS | No execution/data/evidence boundary weakened; `RUNTIME_V2_ENABLED` default unchanged; no B11 frozen contract touched |
| Regression | PASS | Full suite: 1965P/2F/13S, only inherited dashboard failures |
| Parity exit criteria | PASS with documented residual | Core Legacy capabilities preserved or migrated with governance; residual: unified call counting across python/react executors deferred to R3 (see 01/02 docs) |
| Golden matrix | PASS | 12/12 scenarios mapped to real tests (03 doc); matrix limitation documented |

## Exit criteria checklist (V1.1 §8.5)

- [x] Core Legacy capability migration complete (12 capabilities audited, verdicts recorded)
- [x] All migrated items tested (budget adapter + real parity suite)
- [x] No major behavior regression (full suite unchanged except new passes)
- [x] Security boundary not bypassed by capability migration
- [x] Unified Runtime does not depend on unsafe Legacy mechanisms (no code copied; budget added)
- [x] Legacy vs Unified differences concluded (01/02 docs, residual documented)
- [x] Key Golden Matrix passes
- [x] Migration report complete (this directory)
