# R0a-1C Artifact and State Provenance

## Layered Status Separation (R0a-1B → R0a-1C)

| Layer | Where | Status |
| --- | --- | --- |
| Execution success | WorkerResult / ExecutionResult | `succeeded` |
| Docker isolation | container runtime config (R0a-1B, inspect-tested) | enforced at launch |
| Artifact validation | `_collect_validate_publish` + `_accept_artifacts` | validated/rejected per artifact |
| Evidence candidate | collect_* / adapters | unverified |
| Evidence verification | EvidenceRegistry.verify | verified / failed |
| Persistence integrity | runtime/session state + restore revalidation | A1.4.1 revalidation |
| Restore validation | context restore revalidation | per-record reasons |
| Consumption eligibility | report/finding gates | verified + linked verification |

`success = true` is never collapsed into evidence trust: the Docker worker result contains no
evidence object; evidence is created only by runtime evidence paths that apply their own rules.

## State Artifacts (`state__*` / `.analysis_state/`)

- State artifacts travel a dedicated channel (`WorkerResult.state_files`) and are never exposed
  as user artifacts or as evidence; `split_result_artifacts` separates them by the reserved
  prefix.
- Decoders are data-only (parquet / npy with `allow_pickle=False` / JSON); no object
  deserialization, no path-controlled writes (restore goes into the namespace only).
- A malformed or unexpected state artifact fails closed (`ProductStateError`); it never becomes
  evidence.
- Snapshot behavior (plan/yield/restore pollution) is governed by the frozen B-series contracts;
  R0a-1C added no parallel mechanism and found no state→evidence promotion path (verified by
  scan: `state__` files are never referenced by any evidence model).

## Artifact Provenance Fields (assessment)

Current artifact references carry `artifact_id`, `relative_path`, `size_bytes`, `sha256`, and
worker results carry `exit_code`, duration, and validated input asset ids. The following were
evaluated for evidence provenance:

- `execution_id` — already present on all evidence models;
- `worker_backend` / `worker_image_identity` — **not yet recorded** on evidence; the worker
  result does not currently surface image identity to the evidence plane. Recorded as a
  R0a Gate follow-up rather than adding speculative fields now;
- `worker_image_digest` — **tag-only anchoring today** (`deepanalyze-worker:1.1.0`). Per task
  §12.3, the tag must not be claimed as immutable identity. No digest is recorded anywhere and
  none was fabricated; digest anchoring is an explicit R0a Gate handoff item.

## Ordinary Artifacts vs Evidence

Ordinary user artifacts are copied into the session workspace by `_accept_artifacts` and are
never converted into evidence objects automatically. Evidence creation requires the explicit
runtime evidence paths (Phase 3 inventory). Verified by code trace: no call site constructs
evidence from `WorkerResult.artifacts` directly.
