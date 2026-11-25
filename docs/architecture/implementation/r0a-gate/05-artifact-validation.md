# R0a Gate Artifact Validation and State Channel

## Artifact Gate (Phase 8)

Artifact validation is a hard precondition for evidence creation: only accepted artifacts are
copied into the session workspace; evidence assets are registered from accepted files with
content hashes; canonical verification cross-checks artifact IDs and asset provenance.

| Artifact type | Validation | Hash | Consumption |
| --- | --- | --- | --- |
| parquet/csv (user) | extension allowlist, size/count, sha256, symlink rejection | manifest + per-artifact | registered asset (hash-bound) |
| PNG/SVG charts | same allowlist | yes | finding source_artifacts |
| JSON/NPY state (`state__*`) | dedicated channel, data-only decode | manifest | never user artifacts, never evidence |
| oversized/invalid | rejected before publish (atomic) | — | no downstream entry |

Negative probes (missing, corrupt, traversal, symlink, oversized, hash mismatch, duplicate ID,
cross-job reference, validation exception) are covered by `test_container_manager.py`,
`test_docker_worker_backend.py`, the backend validation chain and Gate E2E-N18. All fail closed.

## State ≠ Evidence (Phase 7)

- `state__*` artifacts travel `WorkerResult.state_files`; `split_result_artifacts` separates them
  by the reserved prefix; they are never written to the session workspace.
- Gate E2E-N14: a real state roundtrip produces **zero** user artifacts; a state-content-derived
  canonical evidence cannot be verified (legacy type without content validator → failed).
- No pickle: parquet / npy (`allow_pickle=False`) / JSON only; malformed state fails closed
  (`ProductStateError`), and failed jobs never write back state.
