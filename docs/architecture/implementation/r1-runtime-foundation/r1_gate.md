# R1 Regression & Gate

## Final full regression

```text
2 failed, 1958 passed, 13 skipped, 117 warnings, 4 subtests passed in 1014.55s
```

Only failures remain the frozen A1.3.2 Dashboard baseline:

```text
tests/test_dashboard.py::test_individual_metrics_endpoints
tests/test_dashboard.py::test_dashboard_display_format
```

## Delta

- Stage B: 1952 passed / 2 failed / 13 skipped
- Stage C: 1958 passed / 2 failed / 13 skipped
- Delta: +6 passed, exactly the six new R1 Runtime Foundation tests.
- No new failed or skipped test.

## Gate

| Gate | Result |
| --- | --- |
| Scope compliance | PASS — foundation-only, no architecture sprawl |
| Code review | PASS — additive identity contract; no execution/data/evidence trust bypass |
| Test quality | PASS — positive, mismatch, legacy migration and worker boundary tests |
| Regression | PASS — no new failure in full suite |
| Independent review | PASS — owner metadata has no authorization/trust effect |
| Documentation | PASS |
| Git state | PASS after commit |

## Decision

**R1 RUNTIME FOUNDATION GATE PASS.**

R2 Capability Inventory & Convergence is unlocked.