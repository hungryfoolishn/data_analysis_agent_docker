# R0b Identity & Path Boundary Audit

## Authority and baseline

- Authority: `docs/202611/DeepAnalyze Architecture Convergence Blueprint V1.1.md`, risks F9/F11.
- Stage A frozen baseline: `cbaecd4`; Stage A freeze commit: `4ee8f00`.
- Branch: `codex/architecture-convergence-v1.1-e2e`.
- Worktree: `/python/pragrams/data_analysis_agent_convergence`.

## Findings before R0b

| ID | Finding | Evidence | Severity | R0b decision |
| --- | --- | --- | ---: | --- |
| B-01 | `get_session_workspace()` concatenated arbitrary `session_id` directly under `WORKSPACE_DIR`, allowing traversal and absolute-path-shaped identifiers to escape or attack host paths. | former `api_server_langgraph.py:464-467` | P0 | FIXED |
| B-02 | API bearer token authenticated a deployment but carried no owner semantics; session ownership was not checked on read/write/delete. | `api_security_middleware`, `_check_session`, session endpoints | P1 (P0 for multi-user) | FIXED with single-owner model |
| B-03 | Workspace downloads used resolved containment after joining, but did not reject symlink traversal uniformly and depended on the unsafe session root. | `_session_file_response` | P0 with B-01 | FIXED |
| B-04 | Persisted stale-session cleanup removed the stored path without validating it belonged to the canonical workspace. | `_prune_stale_sessions`, `_cleanup_session` | P0 | FIXED |
| B-05 | Run endpoints loaded archived snapshots by `run_id` without checking the owning session. | `RunHistoryStore.get_run` consumers | P1 | FIXED |
| B-06 | Client-provided `file_path` could name an arbitrary host file and be passed to analysis. | `_resolve_data_file` | P0 | FIXED |
| B-07 | Runtime history/package/report paths were built from persisted session IDs and did not consistently use canonical path validation. | `runtime/history.py`, `runtime/package.py`, `runtime/report_rebuild.py` | P1 | FIXED |

## R0b design

R0b deliberately does not introduce IAM/RBAC. It uses the smallest identity model that satisfies
the V1.1 single-deployment boundary:

1. `session_id` is a locator only and must match `^[A-Za-z0-9_-]{8,64}$`.
2. Owner identity comes only from trusted server/operator configuration. The current source is
   `DEEPANALYZE_API_OWNER_ID`; otherwise a stable `deployment-owner` is used.
3. Session records persist `owner_id`; access is `(owner_id, session_id)`.
4. All workspace lookup uses `execution.path_safety.safe_workspace_join`.
5. Invalid or unsafe persisted records are skipped; cleanup never deletes an unvalidated path.
6. Archived runs with live session metadata enforce ownership. Legacy snapshots without owner
   metadata remain accessible only to the authenticated deployment owner for compatibility.
7. Client `file_path` cannot refer outside the session workspace. Internal retry paths may resolve
   an absolute path only after canonical containment validation.

## Changed paths

- `langgraph_langchain/security.py`
- `langgraph_langchain/api_server_langgraph.py`
- `tests/test_r0b_identity_path_boundary.py`
- `langgraph_langchain/runtime/history.py`
- `langgraph_langchain/runtime/package.py`
- `langgraph_langchain/runtime/report_rebuild.py`

No frozen B8/B10/B11 contracts, Runtime V2 default routing, Docker worker contract, or Evidence
trust model was changed.