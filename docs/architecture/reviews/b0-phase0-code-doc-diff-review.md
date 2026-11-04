# B0: Phase 0 Docs vs Current Code Diff Review

Date: 2026-11-04
Branch: `codex/phase1-m2.4-sqlite-schema`
HEAD at review: `0f4001e feat(runtime): persist sqlite asset snapshots`
Working tree: clean (only pre-existing untracked files unrelated to B0)

## 1. Purpose

Reconcile the Phase 0 product/architecture documents with the actually
committed code, and define the entry condition for B1 / M2.7.3. This batch is
documentation-only: no runtime code was changed.

## 2. Key Finding: The Baseline Moved

The B0 plan was drafted against commit `f695bf0` (M2.7.1 inspection) and
assumed M2.7.2 SnapshotStore was not yet implemented. The branch has since
advanced by two commits:

| Commit | Subject | Meaning |
| --- | --- | --- |
| `f9f15de` | feat(runtime): add sqlite dataframe snapshot store | `data/snapshots.py` + `tests/test_snapshot_store.py` + design doc |
| `0f4001e` | feat(runtime): persist sqlite asset snapshots | `runtime/context.py` integration + runtime sqlite asset tests |

**M2.7.2 is therefore implemented and tested.** All B0 conclusions were
re-derived from the actual HEAD.

## 3. Verified Current Capability Map (code, not intent)

| Capability | Code | Status |
| --- | --- | --- |
| SQLite connection, read-only enforcement, introspection, bounded execution | `langgraph_langchain/data/database.py` | Done (M2.4/M2.5) |
| Query-result DataAsset registration with provenance | `runtime/context.py` (M2.6) | Done |
| Immutable Parquet SnapshotStore (save/load/exists/delete, hash + shape verification, path traversal protection) | `langgraph_langchain/data/snapshots.py` | Done (M2.7.2) |
| Runtime registers SQLite results with verified snapshots; restart recovery of snapshot references | `runtime/context.py` + `tests/test_runtime_sqlite_query_asset.py` | Done (M2.7.2) |
| Executor resolves `input_asset_ids` into DataFrames | only `task_models.py`/`models.py` pass the ID list through; no resolution anywhere | **Missing (M2.7.3)** |
| Read-only SQL tool | absent from `tools/` | Missing (M6) |
| Generic planner wired into default chat path | missing | Missing (M4) |
| Runtime V2 default path | `config.py`: `RUNTIME_V2_ENABLED` defaults `false`; `langgraph_agent.py:1806` branches on it | Legacy ReAct remains default (as documented) |
| Financial domain | `runtime/financial*` | Frozen, unchanged |

## 4. Doc/Code Differences Found And Fixed

1. `docs/product/roadmap.md` — still described M2 as access-layer-only work.
   Updated: M2 core complete through M2.7.2, next is M2.7.3.
2. `docs/product/phase0-summary.md` — claimed "No database adapter". Updated:
   adapter + assets + snapshots exist; the remaining gap is executor-side
   snapshot consumption.
3. `docs/architecture/module-classification.md` — basis commit `5314055`
   predates the SQLite/snapshot work ("no SQL adapter yet"). Updated basis and
   added `data/database.py` and `data/snapshots.py` rows; refreshed the
   `runtime/context.py` responsibility note.
4. `docs/roadmap/phase1-general-analysis-mvp.md` — M2 milestone had no status.
   Added per-item status, added M2.7.3 (snapshot consumption / AssetResolver)
   and M2.7.4 (restart-recovery e2e) as explicit next milestones.
5. `docs/architecture/runtime-v2-default-gate.md` — documented a discovered
   policy gap: `RUNTIME_V2_FALLBACK_ENABLED` defaults to `true` in
   `config.py`, while the gate policy requires fallback to be explicitly
   configured. Recorded as a known gap for the M5a gate work; not fixed in B0
   because changing the default is a runtime behavior change.

## 5. Documents Reviewed And Unchanged (still accurate)

- `docs/product/product-vision.md` — vision unchanged.
- `docs/product/phase0-scope-freeze.md` — freeze rules still valid.
- `docs/product/scope-and-non-goals.md` — still valid.
- `docs/product/acceptance/phase1-golden-contract.md` — Golden Contract,
  Dataset, and Questions contracts still apply; actual manifests remain
  M2/M3/M8 deliverables.
- `docs/architecture/phase0-issue-register.md` — P1-002 (database adapter) is
  now substantially resolved at the adapter layer but remains open at the
  tool/planner/e2e layers; no register rewrite in B0.
- `docs/architecture/M2.7.1-runtime-sqlite-asset-consumption-inspection.md` —
  still accurate for its baseline (`533e8bf`); M2.7.2 has since closed the
  snapshot persistence gap it identified.

## 6. Phase 1 Blockers And Order

| Priority | Blocker | Dependency |
| --- | --- | --- |
| P0 | M2.7.3: resolve `input_asset_ids` to snapshot DataFrames before executor dispatch | M2.7.2 (done) |
| P0 | M2.7.4: restart + resume consumes snapshots without SQL re-execution | M2.7.3 |
| P1 | M4 generic planner (file path first) | M3 understanding service |
| P1 | M6 read-only SQL tool + evidence wiring | M2.7.3 |
| P1 | Fallback default policy resolution (see gate doc gap) | M5a gate |
| P2 | M5a file-path enablement, M5b/M8 overall gate + golden e2e | M4, M6, M7 |

Recommended implementation order: M2.7.3 → M2.7.4 → M3 → M4 → M5a → M6 →
M7 → M5b/M8.

## 7. B0 Acceptance And B1 Entry Condition

- [x] Baseline confirmed as `0f4001e`, working tree clean for tracked files.
- [x] Capability map updated from code.
- [x] Runtime path and fallback gate docs reconciled.
- [x] Phase 1 blockers and order recorded.
- [x] `git diff --check` passes; no runtime code changes.

B1 (M2.7.3) may start immediately: the interface boundary is clear
(resolve `SnapshotReference` from `DataAsset.source_metadata` and hand the
DataFrame to the executor request path), and it does not depend on planner
design decisions.

## 8. Explicitly Not Done (per B0 scope)

- No runtime code changes; no AssetResolver implementation.
- No default-path switch; no fallback default change.
- No financial semantics; no new domain code.
- No rewrite of vision/scope documents; no P2/P3 backlog changes.
