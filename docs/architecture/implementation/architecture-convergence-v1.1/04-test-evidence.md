# R0b Test Evidence

## New R0b matrix

`tests/test_r0b_identity_path_boundary.py` covers:

- valid and invalid session IDs;
- traversal, absolute path, encoded traversal, UNC and symlink escape;
- owner-missing, cross-owner read/write/delete denial;
- owner-authorized access after operator owner context;
- unsafe persisted session quarantine without external deletion;
- data file absolute/traversal denial;
- live cross-owner run denial and owner-authorized run access.

## Regression executed

```bash
python -m pytest \
  tests/test_run_history.py \
  tests/test_r0b_identity_path_boundary.py \
  tests/test_api_security.py -q
```

Final related suite result: **37 passed**. Full repository regression is recorded in
`04-regression-and-gate.md`.

## Compatibility checks

- Runtime V2 default routing unchanged.
- Existing API security tests pass.
- Existing report rebuild tests pass.
- R0a execution/evidence Gate tests pass.