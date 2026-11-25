# R0a Gate Full Regression and Baseline Comparison

## Final result

```text
2 failed, 1944 passed, 13 skipped, 117 warnings, 4 subtests passed
```

Duration: `1087.04s` (0:18:07).

Command:

```bash
conda activate analysis
python -m pytest tests/ -q
```

## Baseline comparison

| Metric | R0a-1C historical baseline | R0a Gate result | Delta |
| --- | ---: | ---: | ---: |
| Passed | 1935 | 1944 | +9 |
| Failed | 2 | 2 | 0 |
| Skipped | 13 | 13 | 0 |
| New Gate E2E | not present | 9 | +9 |

The +9 delta is exactly the new Gate E2E test file. No historical test was removed or skipped to
produce this result.

## Failures

Both failures reproduce the frozen A1.3.2 Dashboard baseline and are unchanged:

```text
tests/test_dashboard.py::test_individual_metrics_endpoints
tests/test_dashboard.py::test_dashboard_display_format
```

No new regression and no new flaky failure was observed in the Gate run.

## Scope levels executed

| Level | Scope | Result |
| --- | --- | --- |
| 1 | `tests/test_r0a_gate_e2e.py` | 9 passed |
| 2 | evidence / restore / worker / policy / docker security suites | green |
| 3 | three product entries + session + finding/report | 223 passed (affected set) |
| 4 | B8/B10/B11 financial/golden/benchmark/macro | green in full run |
| 5 | full repository regression | 1944 passed / 2 known failed / 13 skipped |

## Compatibility

- `RUNTIME_V2_ENABLED` default unchanged.
- B11 frozen contracts unchanged.
- V1 user-visible workflow unchanged except the security-required metadata-only trust downgrade.
- Docker remains the default untrusted execution path; process execution remains explicit opt-in.
