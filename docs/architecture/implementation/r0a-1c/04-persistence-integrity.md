# R0a-1C Persistence Integrity

## Evidence Persistence Paths (real code)

1. **Runtime V2 state** — `AnalysisRuntime._persist()` serializes runtime state (assets,
   executions, verifications, evidence, artifacts) into the workspace. Restore is handled by
   `AnalysisRuntime` state loading plus the A1.4.1 restore-integrity revalidation
   (`context.py:2486`: `_revalidate_restored_canonical_evidence()` and
   `_revalidate_restored_evidence()`).
2. **V1 session state** — `SessionPersistence.save()` writes atomic JSON
   (`workspace/<session_id>/.session_state.json`, tmp + `os.replace` + fsync). Evidence rides
   inside `findings`. Restore previously trusted the persisted `verification_status` verbatim
   — **fixed in R0a-1C** (restore downgrades to `unverified`).
3. **Canonical registry** — in-memory registry re-populated through `restore_record()` which
   resets trust; no direct SQLite storage of canonical evidence exists (the canonical evidence
   plane is runtime-lifecycle state, persisted as part of runtime state).

Note: `evidence/sqlite.py` is the **SQLite-query evidence model** (evidence about SQL reads), not
an evidence persistence store — a naming trap checked and documented.

## Integrity Assessment

- Status, provenance, hashes and timestamps are serialized with the record; there is no separate
  verified-flag table, so no partial write divergence between "status" and "content" stores.
- Session state writes are atomic (temp file + atomic replace); a failed save leaves the previous
  state intact (no partial write).
- Content hashes on assets (`hash_dataframe` / `hash_file`) bind evidence output_asset_ids to
  analyzable content; the runtime restore revalidation recomputes them (snapshot or file hash)
  and downgrades evidence when integrity fails.

## Hash Semantics

Hashes cover asset content (dataframe canonical hash or file stream hash). They prove
content identity/integrity of the registered asset — **not** the correctness of the analysis
conclusion, and not source authenticity. This distinction is enforced by requiring the runtime
content validator (execution linkage, row/column consistency, provenance) on top of hashes before
`verified`.

## Transaction Assessment

Evidence creation and verification are single-object operations over in-memory models persisted
by state snapshots; no multi-table transaction inconsistency is reachable. The atomic state-file
write covers the persistence boundary. No additional transaction machinery was added.
