# R0a-1B Risk Register and Open Issues

## Known Limitations (accepted for this work package)

| ID | Risk | Severity | Mitigation / Plan |
| --- | --- | --- | --- |
| K-1 | `WORKER_BACKEND=process` remains an explicit opt-in that executes on the host process (required by the conda dev/test environment) | Medium (dev only) | Documented; production compose defaults to docker; R0a-1C target is removing non-container production paths |
| K-2 | Worker `_log` diagnostics are only visible via container stderr (not in the job result envelope) | Low | Use `docker logs`; structured job logs proposed for R0a-1C |
| K-3 | Image pinned by tag `1.1.0`, not digest | Low | Digest pinning tracked as follow-up hardening |
| K-4 | `max_artifact_bytes` not per-job configurable from the V2 request contract | Low | Default 100 MiB; extend `TaskExecutionRequest` when needed |
| K-5 | State variable count limited (`MAX_STATE_VARS`); excess variables skipped with a log line | Low | Visible limit, fail-soft for scalars only; DataFrames above limit would be missing — acceptable for current session sizes, revisit if sessions grow |

## Open Items for R0a-1C

1. Network/egress isolation layer for the worker image runtime environment (A1.3.3.2 heritage:
   worker containers already run with `NetworkMode=none`; verify compose-level parity for any
   future worker that needs controlled egress).
2. Secret isolation: ensure no env/secret material is reachable by job containers (current
   `SAFE_ENV` scrub is in place; formalize the allowlist test).
3. Per-job structured logging channel.
4. Image digest pinning + registry provenance.

## Explicitly Out of Scope (unchanged)

- Runtime V2 default switch (`RUNTIME_V2_ENABLED`) — remains off.
- B11 frozen contracts — untouched.
- A1.3.2 freeze baseline and its 2 known dashboard failures — untouched.
