# R0a Gate Baseline

## Environment (verified at gate start)

| Item | Value |
| --- | --- |
| Hostname | `l` (docker-host) |
| Repository root (1C worktree) | `/python/pragrams/data_analysis_agent_r0a1a` |
| Gate worktree | `/python/pragrams/data_analysis_agent_r0a_gate` (created fresh, dir was free) |
| Gate branch | `codex/r0a-gate-e2e-independent-validation` |
| Gate starting commit | `a802571` (R0a-1C freeze) |
| Working tree at start | clean (0 modified, 0 untracked) |
| Python (test env) | conda `analysis`, Python 3.10.18 |
| Docker | 27.1.1, daemon running |
| Worker image | `deepanalyze-worker:1.1.0` (image ID `1ce20d25b8d8`, built 2026-11-24) |

## Inheritance Chain (verified with `git merge-base --is-ancestor`)

```
4a9b254 (A1.3.2 freeze docs)
  -> 83c8bfa  R0a-1A Execution Entry Hardening          [in ancestry]
  -> 4878adc  R0a-1B Docker Worker Integration          [in ancestry]
  -> a802571  R0a-1C Evidence Trust Boundary            [HEAD at gate start]
```

## Historical Test Baseline (R0a-1C report)

- 1935 passed / 2 failed / 13 skipped
- Known failures: `tests/test_dashboard.py::test_individual_metrics_endpoints`,
  `tests/test_dashboard.py::test_dashboard_display_format` (A1.3.2 frozen baseline, reproduced
  at clean `4a9b254`).

## Gate Constraints Honored

- No reset / stash / cleanup of any user workspace; user local checkout untouched.
- No modification of `main`, `codex/a1.3.3-*`, A1.3.2 freeze, or any other task branch.
- `RUNTIME_V2_ENABLED` default unchanged; B11 frozen contracts unchanged; V1/V2 user-visible
  semantics preserved except the security adjudications documented in this gate.
