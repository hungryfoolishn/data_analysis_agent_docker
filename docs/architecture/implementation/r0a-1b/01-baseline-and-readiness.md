# R0a-1B Baseline and Readiness

## Pre-Implementation Baseline

- R0a-1A freeze commit: `83c8bfa` on branch `codex/r0a-1b-docker-worker-integration`
  (branch created from the R0a-1A head).
- R0a-1A guarantees: execution entry hardening — inline/process entry blocked on the untrusted
  policy path, backend selection policy-controlled and fail closed, delegate/python-task entries
  routed through `resolve_execution_backend(UNTRUSTED)`.
- Docker foundation (A1.4.2.2.x): `ContainerManager` per-job isolated workspace
  (`/tmp/deepanalyze-worker-jobs/<job_id>/{input,output,tmp,collected}`), manifest-based input
  integrity, protocol v1 envelopes, container hardening (non-root, no network, readonly rootfs).
- Existing tests at baseline: docker gate suite green; no product-path E2E existed.

## Readiness Checks Performed

1. Docker daemon 27.1.1 reachable on the server; `deepanalyze-worker:1.1.0` buildable
   (built during implementation, ~640 MB).
2. conda env `analysis` (Python 3.10.18) usable for test driving.
3. R0a-1A entry hardening present at all three entries (verified by reading the call sites).
4. B11 frozen contracts and Runtime V2 default switch untouched (verified by `git diff` scope).

## Constraints Honored

- No Process/Inline fallback was added; docker failures surface as job failures.
- No mock substituted for real Docker E2E; only the daemon-availability skip guard is used.
- The server worktree is the only modified checkout; the user local checkout was not touched
  during implementation.
