# R1 Identity, Execution, Data, Evidence and State Governance

## Identity

| Boundary | Control |
| --- | --- |
| Source | Operator/server `DEEPANALYZE_API_OWNER_ID` or stable `deployment-owner`; never request body |
| Session boundary | R0b `(owner_id, session_id)` authorization |
| Runtime | `RuntimeIdentityContext.create(session_id, owner_id)` |
| Persistence | `AnalysisTask.owner_id` persisted in task snapshot |
| Restore | Owner mismatch raises `runtime identity mismatch during restore`; ownerless legacy snapshot migrates only with explicit owner |
| Worker | `WorkerJob.owner_id` as audit metadata |
| Tools | Delegate and Python task executor propagate owner from session/task |
| Trust | Owner identity never promotes Evidence or bypasses execution policy |

## Execution governance

- All product entries remain behind `resolve_execution_backend(UNTRUSTED)`.
- Docker remains the only production untrusted backend; process remains explicit opt-in.
- No new execution entry was added by R1.
- Timeout, cancellation, resource limits, image identity and no-network behavior remain R0a frozen.

## Data governance

R1 reuses the existing Data Platform:

- `FileDataSource`, `DatabaseDataSource`, `MemoryDataSource`, bounded reads and cache tests.
- `DataAsset` content hashing and provenance remain unchanged.
- AssetResolver and SQLite query assets continue to validate source identity.
- Session file inputs are restricted to the session workspace by R0b before Runtime sees them.

## Evidence governance

- `EvidenceRegistry` remains the only verification authority.
- Content validators are mandatory; missing/exception/unknown validation fails closed.
- Persistence/restore revalidation remains R0a frozen.
- R1 owner metadata does not affect trust status.

## State and persistence

- Runtime snapshot remains versioned and includes task owner identity when present.
- `RunHistoryStore` accepts owner-bearing tasks while preserving old snapshots.
- Restore integrity tests cover asset hashes, artifact existence, evidence revalidation and state
  corruption.
- `RunHistoryStore` and report/package paths continue to use R0b safe session joins.

## Compatibility

- Existing Runtime tests using short internal session IDs continue to work; Runtime identity rejects
  only empty/path-like session values. API session IDs remain under the stricter R0b whitelist.
- Ownerless legacy snapshots remain readable for single-owner compatibility.
- No user-visible API response shape changed.