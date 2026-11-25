# Architecture Coherence Audit

## Authority map (actual single sources)

| Domain | Authority | Duplicates found |
| --- | --- | --- |
| Runtime selection | `runtime/runtime_route.py` | none |
| Execution backend selection | `execution/execution_policy.py::resolve_execution_backend` | `worker_backend_from_env` exists for operator construction; not used by untrusted entry points |
| Tool budget (V2 stream) | per-run `ToolCallBudget` at `TaskExecutor` | financial path lacks wiring (F-2, bounded) |
| Capability definition | tool registry + contract test lock | none |
| Identity/ownership | `security.py` + `RuntimeIdentityContext` + API owner checks | none |
| Data access | `runtime/asset_resolver.py` + SnapshotStore | none |
| Evidence | `EvidenceCollector` + `VerificationPolicy` (single factory wiring) | none |
| Execution state | scheduler + runner states | none |
| Session state | API `SESSIONS` + persisted metadata w/ owner validation | none |
| Evaluation | `runtime/evaluation/` + deterministic evaluator | none |

## Legacy dependencies (classified)

- Real tool objects reused by V2 via `StructuredTaskExecutor` — necessary adapter
  (prevents duplicate tool implementations).
- React executor wraps prebuilt agent — necessary adapter behind V2 task interface.
- Shared python namespace (`session.ns`) — transitional shared utility with lifecycle
  bound to timeout/workspace confinement; size limits deferred (documented).
- Legacy default route remains (`RUNTIME_V2_ENABLED` off) — deliberate, not hidden;
  V2 is opt-in with hard-stop semantics.

No undocumented coupling or architectural violation found. No duplicate policy layers:
both runtimes reach the same execution policy, verification policy, and evidence factory.

## Duplicate governance check

Grep-verified single definitions for execution policy, verification policy, evidence
collection wiring, asset resolution, and budget. The only remaining parallel structure is
the Legacy ReAct loop itself, which V1.1 explicitly preserves until parity-driven removal
is a separate approved stage.

## Drift risk & owner

Adapters are small and contract-tested. Registry drift is locked by
`test_capability_inventory.py`; budget drift is locked by dispatcher tests. Owners are the
respective modules; removal is not scheduled within V1.1 (explicitly out of scope).

## Verdict

**COHERENT WITH TRANSITIONAL ADAPTERS** — no material architectural contradiction; the
declared convergence claim (one governance plane, adapters for reuse, bounded residual
divergence) is accurate.
