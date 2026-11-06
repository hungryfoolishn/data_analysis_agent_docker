# B10 Security Inspection (Pre-hardening)

Review baseline: `0c24290e95ad5b4095187130e4b93983e9e4aa3e`  
Branch: `codex/phase1-m2.4-sqlite-schema`  
Scope: API authentication, session/workspace boundary, upload flow, and deployment configuration.

## Route inventory

| Route | Before B10 | Risk |
| --- | --- | --- |
| `GET /health` | Public | Low; intended health probe |
| `POST /release/candidate/assess` | Auth middleware, but empty token bypassed | High when unauthenticated |
| `POST /v1/files` | Empty token bypass; whole-file memory read; global workspace | High |
| `POST /workspace/upload` | Empty token bypass; whole-file memory read | High |
| `GET /workspace/files` | Optional global listing | High |
| `GET /workspace/files/{filename:path}` | Global workspace resolution; no session authorization | High |
| `DELETE /sessions/{session_id}` | Empty token bypass | High |
| `GET/POST /sessions/{session_id}/*` | Empty token bypass | High |
| `GET /metrics/*`, `GET /traces/*` | Empty token bypass | High |
| `/analysis/*`, `/v1/chat/completions` | Empty token bypass | High |

## Authentication findings

- `verify_bearer_token()` returned `True` whenever `API_AUTH_TOKEN` was empty.
- `docker-compose.yml` defaulted `API_AUTH_TOKEN` to empty.
- There was no production startup gate for missing credentials.
- Constant-time comparison was already used when a token was present.

## Session/workspace findings

- `/workspace/files` could list global workspace files when `session_id` was omitted.
- `/workspace/files/{filename:path}` resolved any file under the global workspace root.
- There was no session-scoped download boundary.
- A symlink inside a session could escape to an outside file.
- `.sessions.json`, `.analysis_tasks.json`, and other root metadata were reachable in principle through path traversal.

## Upload findings

- Both upload endpoints called `await file.read()` before checking size.
- The size check therefore could not prevent peak memory pressure.
- Temp-file cleanup and atomic final replacement were absent.
- Filename sanitization existed, but content-type validation was extension-only.
- There was no upload concurrency limit.

## Deployment findings

- Docker Compose did not expose Runtime V2 policy variables.
- Production and development modes were not explicit.
- No startup guard required a token in production.
- Streamlit saved uploads with an unsanitized local filename before forwarding to API.

## Decision

B10 hardening is required before any further product default-switch work.