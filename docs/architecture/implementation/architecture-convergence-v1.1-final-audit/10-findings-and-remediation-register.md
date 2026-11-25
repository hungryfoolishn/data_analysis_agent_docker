# Findings & Remediation Register

| ID | Domain | Severity | Location / Commit | Evidence | Expected | Actual | Impact | Root cause | Remediation | Regression test | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F-1 | R3 budget | MEDIUM | `runtime/capability_adapter.py` @ `cfa6c79` | `consume()` performed unlocked check-then-increment on `Counter` | Budget accounting must be exact under any dispatch concurrency | Safe only under sequential scheduler (`max_running_tasks=1`); a future concurrent dispatcher could over-admit | Latent over-admission of governed tool calls | Budget written for sequential dispatch without internal synchronization | Added `threading.Lock` around check-and-increment, `remaining`, and `snapshot` (commit `3b13a96`) | `test_concurrent_consumers_cannot_over_admit` (8 threads racing a 50-call budget: exactly 50 admitted, rest rejected) | CLOSED |
| F-2 | R3 budget | MEDIUM (accepted) | `runtime/graph.py::build_runtime_v2_controller`, `runtime/financial/runtime_integration.py:95` | Alternate controller construction does not wire `call_budget` | Claim: budget is the single enforcement point for all governed dispatch | Financial domain runtime path has no unified tool budget | Alternate bounded execution path without per-run call budget | Financial pack predates R3; V1.1 scope targeted the Legacy-V2 bridge | Accepted with bound: template plans are deterministic and capped by `AnalysisPlan.validate_steps` (max_steps default 20, hard cap 200); wiring the budget into the financial pack would touch B10-frozen code — recommended follow-up, not required for V1.1 closure | n/a (documented) | ACCEPTED |
| F-3 | Documentation | LOW | R3 stage docs (`02-intelligence-evolution.md`) | Wording implies no alternate execution paths exist outside budget | Precise claim | Overstated: financial path exists (F-2) | Documentation precision | Drafted before F-2 discovery | Corrected by this audit register and closure report; stage docs remain as historical artifacts of their commits | n/a | CLOSED (corrected in audit) |
| F-4 | Regression | INFO | `tests/test_dashboard.py::test_individual_metrics_endpoints`, `::test_dashboard_display_format` | Both fail at pre-V1.1 baseline `cbaecd4` (reproduced in temp worktree) and at final audit HEAD | Known-frozen failures must be pre-existing and unrelated | Confirmed pre-existing, deterministic, untouched by the six commits | None for V1.1 | Pre-existing dashboard/API issue outside convergence scope | None (remains excluded per frozen baseline policy) | n/a | CONFIRMED PRE-EXISTING |
| F-5 | Golden evaluation | MEDIUM (accepted) | `tests/evaluation/golden_cases/`, `runtime/evaluation/runner.py` | 50 cases have strong numeric ground truth but no adversarial/negative cases | Evaluation useful for future R3 improvement | No case asserts rejection of incorrect analyses end-to-end | Benchmark cannot yet detect false-positive analysis quality | Deferred scope (R3 doc) | Safe default: numeric weight 0.30 + verified-evidence minimums prevent keyword-only passes; smallest next step: add negative cases | n/a | ACCEPTED |

## False completion claims found

None. All material claims audited were supported by code/tests, with two precision
corrections recorded (F-2, F-3).

## Verified claims

- Six-stage chain, scope, and diffs (01).
- R0a fail-closed execution policy, container hardening, image identity (02).
- R0b ownership/identity and real-resolved path containment (02).
- R1 convergence with adapters; single governance authorities (03, 09).
- R2 inventory locked by registry contract test (04).
- R2.5 parity: 8 TRUE / 2 PARTIAL / 1 INTERFACE_ONLY, real-path tests (05).
- R3 budget semantics, rejection matrix, dispatch-point unification (06).
- Regression 1969/2/13 reproduced independently (07).
- Golden evaluation validity with limitations (08).
