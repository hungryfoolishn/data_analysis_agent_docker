# R0a-1B Workspace and Artifact Validation

## Job Workspace Layout (manager-owned)

```
/tmp/deepanalyze-worker-jobs/<job_id>/
  manifest.json      # manager-owned, outside container mounts
  input/             # 0550 root:worker_gid, files 0440 — read-only to worker
  output/            # 0750 worker-owned — writable cwd, collected after exit
  tmp/               # 0750 worker-owned
  collected/         # manager-validated publish target
```

- One workspace per job; duplicate creation rejected (`workspace_reuse_forbidden`).
- Inputs are content copies; originals are never mounted.
- `verify_input_integrity` re-hashes every staged input against the manifest **before** any output
  is accepted, so a mutated input cannot earn artifact publication.

## Artifact Validation Chain

1. Worker declares artifacts (relative_path, size, sha256) in the protocol v1 envelope.
2. `_collect_validate_publish`:
   - count limit (`max_artifacts` = 100);
   - input integrity first;
   - per-artifact: name path-safety, size limit, extension allowlist
     (`.csv .json .txt .md .png .svg .parquet .xlsx .xls .pkl .pickle .npy .npz .html .pdf .log
     .zip .db .sqlite .geojson`), hash verification;
   - manager archive fetch rejects symlink/link/dir members;
   - atomic all-or-nothing publish into `collected/`.
3. `_accept_artifacts`:
   - `state__*` -> dedicated state channel (never written to the session workspace);
   - everything else -> copied into the session workspace via `safe_workspace_join`
     (path traversal impossible by construction).

## E2E Coverage

- `test_artifact_generation_is_accepted_into_workspace`: generated artifact lands in the session
  workspace.
- `test_source_file_is_staged_and_readable`: user source staged into the isolated job input.
- `test_two_jobs_cannot_access_each_others_workspace`: cross-job isolation.
- `test_missing_image_fails_closed_without_process_fallback`: fail-closed behavior.
