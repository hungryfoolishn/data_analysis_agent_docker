# R1 Test Evidence

## New foundation tests

`tests/test_r1_runtime_foundation.py`:

- identity validation and worker metadata;
- owner propagation into `AnalysisTask`;
- owner-preserving restore;
- owner mismatch restore failure;
- ownerless legacy snapshot migration on restore;
- `WorkerJob` owner validation;
- product worker job owner propagation.

## Related regression

Executed before full regression:

```bash
python -m pytest \
  tests/test_r1_runtime_foundation.py \
  tests/test_r0b_identity_path_boundary.py \
  tests/test_api_security.py \
  tests/test_run_history.py \
  tests/test_report_rebuild.py \
  tests/runtime_v2/test_restore_integrity.py \
  tests/test_data_sources.py \
  tests/test_asset_resolver.py \
  tests/test_file_asset_resolver.py \
  tests/test_data_profile.py \
  tests/test_evidence_trust_boundary.py \
  tests/test_r0a_gate_e2e.py -q
```

Result: **155 passed**.

The full repository result is recorded in `04-regression-and-gate.md`.