# R0b Regression & Gate

## Final full regression

Command:

```bash
python -m pytest tests/ -q
```

Result after the final legacy-owner compatibility correction:

```text
2 failed, 1952 passed, 13 skipped, 117 warnings, 4 subtests passed in 1038.31s
```

Only failures:

```text
tests/test_dashboard.py::test_individual_metrics_endpoints
tests/test_dashboard.py::test_dashboard_display_format
```

Both are the frozen A1.3.2 Dashboard baseline. The first full run exposed one new failure,
`tests/test_run_history.py::test_retry_api_starts_new_linked_run_and_preserves_context`. It was
traced to in-memory legacy sessions missing `owner_id`; the loader/authorization compatibility rule
was corrected and the second full regression passed with no new failure.

## Delta

- Stage A baseline: 1944 passed / 2 failed / 13 skipped.
- Stage B result: 1952 passed / 2 failed / 13 skipped.
- Delta: +8 passed, which matches the 8 new R0b test functions (one symlink API test skips on this
  host when unsupported, represented in the same run).
- Failed/skipped delta: 0.

## Related regression

Final related security/runtime/API suite result: **37 passed**.

## Gate

| Gate | Result |
| --- | --- |
| Scope compliance | PASS — only V1.1 F9/F11 identity/path fixes |
| Code review | PASS — centralized identity/path policy, no cross-stage scope drift |
| Test quality | PASS — positive, negative, cross-owner, persistence and symlink tests |
| Regression | PASS — full suite has no new failure |
| Independent security review | PASS — no remaining Critical/High |
| Documentation | PASS |
| Git state | PASS after commit |

## Decision

**R0b GATE PASS.**

Stage C — R1 Runtime Foundation — is unlocked.