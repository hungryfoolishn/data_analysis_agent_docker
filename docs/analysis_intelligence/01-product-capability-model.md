# Product Capability Model & Responsibility Boundaries (P1 scope definition)

## Capability statement

Analysis Intelligence V1.0 adds a **deterministic semantic constraint layer**
underneath the existing analysis runtime. It does NOT replace the LLM, the
ReAct loop, or the runtime V1/V2 routing. Its product role:

> Every analysis goal passes through a machine-checkable chain
> (semantic registry -> intent -> constraint-checked plan -> verified
> claims) before any number reaches the user, and every number is bound to
> verifiable evidence.

## Capability list (all with real consumers + tests)

| Capability | Entry | Consumer chain |
| --- | --- | --- |
| Semantic contracts (metric/dimension/relationship + trust levels) | `analysis_intelligence/semantic_contracts.py` | IntentResolver, CompatibilityValidator, planner compatibility gate |
| Intent understanding (deterministic, provenance per field) | `analysis_intelligence/intent.py` | IntentPlanner |
| Constraint-aware planning (metric x dimension gate, required columns, granularity) | `analysis_intelligence/constraints.py` | emitted GeneralAnalysisPlan -> validate_general_plan -> runtime execution |
| Plan revision ledger (bounded, goal-rewrite guard) | `analysis_intelligence/constraints.py` | planner outcomes |
| Dynamic schema exploration with fold-back | `analysis_intelligence/exploration.py` | intent state transitions -> planner retry |
| Claim/evidence verification + orchestration state | `analysis_intelligence/reasoning.py` | AnalysisState.decide_next termination contract |
| Evaluation harness (golden + negative + boundary + dynamic) | `analysis_intelligence/evaluation.py` | regression floor for the whole chain |

## Responsibility boundaries (what this layer is NOT)

1. NOT a second planner: it emits `GeneralAnalysisPlan` reusing the frozen
   `validate_general_plan` authority; it never bypasses B8 AcquisitionService,
   SnapshotStore, or the tool registry.
2. NOT an LLM replacement: it constrains and verifies; NL variance handling
   stays with the existing prompt layer.
3. NOT a second evidence system: claims bind to `EvidenceRecord`s produced by
   the established evidence/verification pipeline.
4. NOT a frozen-contract editor: `RUNTIME_V2_ENABLED` default, budget
   semantics, security/policy/container modules untouched.

## Trust discipline (invariant across all capabilities)

- `INFERRED` never passes a deterministic check.
- `DECLARED` is schema-verified but not semantically curated; usable with
  recorded provenance, confidence < 1.0.
- `VERIFIED` is curated + schema-consistent; required for drill-down paths.
- Execution success is never accepted as analysis correctness (P6
  `wrong_analysis_detected` gate).
