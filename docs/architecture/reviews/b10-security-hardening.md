# B10 Security Hardening

Status: complete  
Baseline: `0c24290e95ad5b4095187130e4b93983e9e4aa3e`  
Scope: API authentication, session/workspace isolation, streaming upload, deployment gate.  
Runtime V2 default was not changed.

## Authentication

- Empty configured token no longer authenticates any request.
- Bearer comparison remains constant time.
- `/health` remains public.
- All other routes require a bearer token.
- Production startup fails when `API_AUTH_TOKEN` is empty.
- Production startup fails when `API_ALLOW_INSECURE_AUTH=true`.
- Development can bypass empty-token auth only by explicitly setting both:
  - `API_ENVIRONMENT=development`
  - `API_ALLOW_INSECURE_AUTH=true`

Docker entrypoint now applies the same production startup gate.

## Session / workspace boundary

- `/workspace/files` now requires `session_id`.
- Global workspace listing was removed.
- Download requires a valid session and resolves files only inside that session workspace.
- Session paths are containment-checked with `Path.resolve()` and `relative_to()`.
- Symlink escape is rejected by final-path containment.
- `/v1/files` now requires a non-empty session ID and stores under that session.
- Existing artifact-style URLs that include `<session_id>/<filename>` remain supported and are scoped to the session.
- This is a single-user authorization boundary; a shared bearer token is not a multi-user identity model.

## Streaming upload

Both upload endpoints now:

1. acquire an upload semaphore;
2. generate a unique safe destination filename;
3. stream chunks to a same-directory temp file;
4. enforce `MAX_UPLOAD_SIZE_MB` while streaming;
5. fsync the completed temp file;
6. validate content structure by declared type;
7. atomically replace to the final destination;
8. clean up temp files on failure.

Maximum concurrent uploads default to 5.

## File content validation

- `.csv`: pandas header parse.
- `.xlsx`: ZIP container, workbook structure, and worksheet cell budget.
- `.xls`: OLE2 header.
- `.parquet`: `PAR1` start/footer signature.
- `.sqlite` / `.db`: SQLite header and `PRAGMA quick_check`.
- Unsupported extensions are rejected before write.

## Configuration and deployment

Added:

- `API_ENVIRONMENT`
- `API_ALLOW_INSECURE_AUTH`
- `MAX_CONCURRENT_UPLOADS`

Docker Compose now passes these variables. Docker entrypoint rejects production startup without `API_AUTH_TOKEN`.

## Security tests

New suite:

```text
tests/test_api_security.py
```

Coverage:

- public health;
- missing token;
- empty token;
- invalid token;
- valid token;
- explicit development insecure mode;
- session-required workspace listing;
- session upload and scoped download;
- cross-session denial;
- path traversal, absolute path, and UNC;
- symlink escape;
- oversized streaming upload;
- disguised file type;
- temp cleanup;
- production startup gates.

## Regression results

```bash
python -m pytest -q tests/test_api_security.py tests/test_phase1_runtime_integration.py tests/test_golden_phase1.py tests/test_asset_resolver.py tests/test_file_asset_resolver.py tests/test_runtime_sqlite_recovery_e2e.py tests/test_general_planner.py tests/test_sqlite_sql_tool.py tests/test_analysis_report.py tests/test_runtime_golden_e2e.py tests/test_snapshot_store.py tests/test_runtime_sqlite_query_asset.py tests/test_database_read.py tests/test_database_connection.py tests/test_data_sources.py tests/test_golden_sqlite_fixture.py tests/test_platform_runtime.py
# 169 passed, 2 skipped

python -m pytest -q tests/runtime_v2
# 14 passed
```

## Remaining boundaries

- Bearer token remains a single-service credential, not a per-user principal.
- Multi-user isolation requires an authenticated principal-to-session ownership model.
- Container/microVM sandboxing for Python execution remains a separate B12+ item.
- Docker build/live deployment E2E remains B13.