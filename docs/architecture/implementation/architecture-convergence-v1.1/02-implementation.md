# R0b Identity & Path Boundary Implementation

## Session identity

`security.py` now provides:

- `SESSION_ID_PATTERN = ^[A-Za-z0-9_-]{8,64}$`
- `validate_session_id()`
- `validate_owner_id()`
- `default_owner_id()`

The owner value is operator/server controlled via `DEEPANALYZE_API_OWNER_ID`; request headers,
form fields and JSON bodies cannot define identity.

## Session ownership

Session records now serialize and persist `owner_id`. `get_or_create_session()` and
`_ensure_session_consistency()` enforce the tuple `(default_owner_id(), session_id)`. Ownership
mismatch returns 404 without deleting the record. Direct session deletion performs the same check.

Existing sessions without owner metadata are migrated to `deployment-owner` when loaded. Existing
valid UUID-style IDs remain accepted because they match the whitelist.

## Canonical path boundary

- `_resolve_session_workspace()` validates the session ID and uses `safe_workspace_join()`.
- Symlinked workspace roots and symlink components are rejected.
- `_session_file_response()` uses the same safe join, so traversal, encoded traversal and symlink
  escape cannot reach outside the session.
- `_safe_upload_destination()` continues to use a generated basename, now under a validated
  canonical workspace.

## Persistence and cleanup safety

- `_load_sessions()` skips invalid IDs, invalid owners, unsafe workspace paths and missing
  canonical workspaces.
- `_prune_stale_sessions()` deletes only the canonical session workspace.
- `_cleanup_session()` never deletes a stored path that does not equal the canonical workspace.
- Unsafe persisted records are removed from memory/persistence, but never used as an rmtree target.

## Run and data resource ownership

- Every `/analysis/runs/{run_id}` consumer now loads through `_load_run_snapshot()`.
- If a live session record exists, its owner must match the authenticated deployment owner.
- Legacy run snapshots without a live session remain a compatibility case for the single owner.
- `/analysis/runs` filters summaries to the deployment owner's sessions.
- `_resolve_data_file()` rejects client absolute paths and any traversal outside the session
  workspace. Internal retry resolution is allowed only after canonical containment validation.

## Runtime path hardening

- `RunHistoryStore._snapshot_paths()` validates and safely joins session IDs.
- `build_analysis_package()` uses session ID validation and `safe_workspace_join()`.
- `write_rebuilt_report()` uses the same boundary for rebuild output.

## Compatibility

- Runtime V2 default routing remains unchanged.
- Existing API request/response contracts remain unchanged.
- Existing single-token deployments continue to work under `deployment-owner`.
- Legacy archived runs remain readable to preserve historical behavior.