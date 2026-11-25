# Product Gap & Implementation Map (P0)

## Gaps -> implementation mapping (no rewrites of frozen V1.1)

| # | Gap | Implementation | Phase | Reuse decision |
| --- | --- | --- | --- | --- |
| G1 | Semantic model has no drill-down/hierarchy/verification statuses and no deterministic consumer | `langgraph_langchain/analysis_intelligence/semantic_contracts.py`: MetricContract, DimensionContract, DimensionRelationship (VERIFIED/DECLARED/INFERRED/UNKNOWN/INVALID), CompatibilityValidator, SemanticRegistry, SemanticResolver; bridges from existing `semantic/models.py` | P1 | reuse semantic/models.py via bridge; new package as the deterministic consumer |
| G2 | No intent contract; goal parsing buried in planner regexes | `analysis_intelligence/intent.py`: AnalysisIntent + IntentResolver (deterministic, registry-driven, provenance per field) + completeness states (READY/READY_WITH_ASSUMPTIONS/NEEDS_EXPLORATION/NEEDS_CLARIFICATION/UNSUPPORTED) | P2 | reuse general_planner regex layer via resolver |
| G3 | Plan validation is structural only (cycles/refs/columns), no metric/dimension compatibility | `analysis_intelligence/constraints.py`: validate_plan_against_intent (compatibility + required columns + granularity) wired before initialize_plan; PlanRevision record (bounded count, reason, trigger evidence) | P3 | reuse AnalysisPlan + validate_general_plan |
| G4 | Exploration is unstructured pipeline behavior | `analysis_intelligence/exploration.py`: ExplorationQuestion, ExplorationResult (findings + semantic candidates + next_decision), ExplorationDecider — two divergent-path proof tests | P4 | reuse asset resolver bounded reads + eda/inspect ops |
| G5 | No claim-evidence binding | `analysis_intelligence/reasoning.py`: Claim (FACT/OBSERVATION/HYPOTHESIS/CONCLUSION/LIMITATION), ClaimVerifier (evidence binding checks), AnalysisState container | P5 | reuse EvidenceCollector/VerificationPolicy outputs |
| G6 | No negative-case evaluation | `analysis_intelligence/evaluation.py`: IntelligenceEvaluationCase (positive/negative/boundary), IntelligenceEvaluator (critical gates + weighted score), golden case fixtures incl. rejection cases | P6 | reuse runtime/evaluation runner patterns |

## Implementation plan (order)

P1 semantic contracts (+ tests) -> P2 intent (+ golden tests) -> P3 constraint-aware
planning integration (+ tests) -> P4 exploration feedback (+ divergence tests) ->
P5 reasoning/state (+ tests) -> P6 evaluation harness (+ baseline run) -> E2E validation.

## Risks

- Over-abstraction: mitigated by requiring every new model to have a real consumer + test.
- V1.1 regression: mitigated by additive-only changes; no frozen-file semantics edits.
- Evaluation honesty: negative cases must really fail; scoring separates execution success
  from analysis correctness.

## P0 Gate result

- Baseline code verified (runtime, planner, prompts, tools, skills, semantics, evaluation).
- Freeze commits verified (cfa6c79, 3b13a96, 5f5be80, 13d02f4 all in ancestry).
- No blind rewrites planned; reuse decisions recorded per gap.
- **P0 Gate: PASS.**
