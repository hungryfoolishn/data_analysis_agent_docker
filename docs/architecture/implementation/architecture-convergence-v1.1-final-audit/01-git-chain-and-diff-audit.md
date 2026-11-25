# Git Chain & Diff Audit

## Commit DAG (verified)

```
cbaecd4 (pre-V1.1 base, R0a Gate)
  └─ 4ee8f00 docs(convergence): freeze r0a baseline and adopt v1.1 blueprint
      └─ 16b04c2 feat(security): enforce r0b identity and path boundaries
          └─ 689744c feat(runtime): establish r1 identity-aware runtime foundation
              └─ 36a3900 docs(runtime): add r2 capability inventory and contract tests
                  └─ da2004a feat(runtime): add r2.5 bounded legacy capability adapter
                      └─ cfa6c79 feat(runtime): add r3 unified orchestration budget
```

Linear, single-parent chain A→F. No merge commits, no reverts, no force-push markers.

## Per-commit verification

| Commit | Full SHA | Parent | Claimed scope | Actual content | Verdict |
| --- | --- | --- | --- | --- | --- |
| A `4ee8f00` | `4ee8f00b11abd4f7265b654f434f1563a7174e30` | cbaecd4 | Freeze docs | 3 doc files only (+484) | MATCH — docs-only freeze |
| B `16b04c2` | `16b04c26f3821a4ba2c06b7b513ad447c6cf9174` | 4ee8f00 | Identity/path | security.py (+32), api_server (+276/-), history/package/report_rebuild, 211-line test, 5 docs | MATCH |
| C `689744c` | `689744caab5829f88efc3ddb270cbef801fb9ffc` | 16b04c2 | Runtime foundation | identity.py (new), api/agent/context/models/delegate/python executor/job contract, 103-line test, 4 docs | MATCH |
| D `36a3900` | `36a3900c060880fe8c8fe2b3956118bb663f820b` | 689744c | Inventory | 3 docs + registry contract test only (no prod code) | MATCH |
| E `da2004a` | `da2004a25899e82824769664a43b40b665f3c0a1` | 36a3900 | Legacy parity adapter | capability_adapter.py (new), executor/agent budget wiring, tests + 4 docs | MATCH |
| F `cfa6c79` | `cfa6c79b9a07cd7757b89bf132bc56c6a12d97fe` | da2004a | Unified budget | budget moved to TaskExecutor + context wiring, tests re-anchored, 3 docs | MATCH |

## Diff integrity findings

- No tests deleted or renamed across `cbaecd4..cfa6c79` (diff-filter=DR empty).
- No `pytest.mark.skip`/`skipif` lines added.
- No security assertion relaxation found; Stage B strengthened the API (owner checks,
  session whitelist, path safety), later stages did not touch `security.py` again.
- No unrelated changes bundled; each commit touches only its declared area plus docs.
- No debug bypasses or default-route changes (`RUNTIME_V2_ENABLED` untouched).
- Stage F rewired the R2.5 budget from `StructuredTaskExecutor` to `TaskExecutor`; the
  R2.5 tests were re-anchored accordingly rather than weakened (exhaustion-before-side-effect
  semantics preserved at the dispatcher).

**Verdict: VERIFIED — commit chain and diffs support the claims.**
