# R0a-1B Independent Security Review

Reviewer position: implementation-independent pass over the final diff (Phase 11 reverse review),
performed after all E2E bring-up defects were fixed.

## Reviewed Claims and Verdicts

| # | Claim | Method | Verdict |
| --- | --- | --- | --- |
| 1 | No pickle crosses the container boundary | read `product_job.py` restore/emit paths; entrypoint uses `pd.read_parquet` / `np.load(allow_pickle=False)` / `json` only; `job.json` parsed as JSON | PASS |
| 2 | State cannot overwrite arbitrary host files | restore writes into `self.ns` only; state bytes decoded, never written to disk; `_accept_artifacts` writes only via `safe_workspace_join` and never for `state__*` | PASS |
| 3 | Forged `state__` names cannot escape the state channel | `_accept_artifacts` routes any name with the `state__` prefix to the channel; emit-side variable names must be identifiers ≤64 chars | PASS |
| 4 | Input integrity enforced before output acceptance | `verify_input_integrity` called before publish in `_collect_validate_publish` | PASS |
| 5 | Symlinks cannot exfiltrate or pollute | `_collect_artifacts` rejects links; manager archive fetch rejects link members; mirroring creates links only inside the container-owned cwd | PASS |
| 6 | No fallback to process/inline on docker failure | reviewed all docker-branch error paths; failures return failed results / `[SECURITY_DENIED]`; `test_missing_image_fails_closed...` covers it | PASS |
| 7 | Container cannot reach docker socket / host services | `NetworkMode=none`, no socket bind in worker spec, image has no docker CLI | PASS |
| 8 | Cancellation and timeout terminate the container | dispatcher wait loop honors cancel event; E2E timeout test passes | PASS |
| 9 | B11 / A1.3.2 / A1.3.3 freezes untouched | `git diff` scope review — no contract, no Runtime default switch, no compose network topology change | PASS |
| 10 | Delegate cannot smuggle state between sessions | delegate restores no state; fresh job workspace per call; E2E delegate test | PASS |

## Residual Observations (non-blocking, tracked in risk register)

- Worker `_log` diagnostics go to container stderr; operational debugging needs
  `docker logs` access or a follow-up structured-log channel.
- `max_artifact_bytes` is not per-job configurable from the V2 request contract yet.
- Image uses tag pinning (`1.1.0`), digest pinning deferred (documented in the Dockerfile).
