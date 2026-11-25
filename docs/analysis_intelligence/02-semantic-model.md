# Analysis Intelligence Semantic Model (P1)

Entry: `langgraph_langchain/analysis_intelligence/semantic_contracts.py`
Tests: `tests/test_analysis_intelligence_semantic.py` (13 passed)

## Contracts

| Contract | Key semantics |
| --- | --- |
| `MetricContract` | metric_id, aliases, formula, metric_kind (basic/derived/ratio/rate/weighted/count/distinct_count/average/ranking/statistical), numerator/denominator, aggregation, unit, required_columns, compatible/incompatible_dimensions, supported_granularity, `TrustLevel` source, validation_rules |
| `DimensionContract` | dimension_id, aliases, semantic_type (categorical/temporal/identifier), source (`builtin` / `dataset_defined` / `skill_defined` / `runtime_discovered`), source_column, data_asset_id, cardinality, `ValidationStatus` |
| `DimensionRelationship` | parent/child, RelationshipType (parent_child/mapping/many_to_many/conditional/peer), cardinality, mapping_key, applicable asset/metric, `ValidationStatus` (verified/declared/inferred/unknown/invalid) |
| `CompatibilityReport` | VALID / INVALID / CONDITIONAL / UNKNOWN + reason + preconditions + optional DrillDownPlan |

## Trust discipline (enforced in code)

- `TrustLevel.INFERRED_CANDIDATE` metrics can never carry confidence > 0 and can never
  pass deterministic compatibility checks (returns UNKNOWN with "verification required").
- Ratio/Rate metrics REQUIRE numerator + denominator AND explicit validation rules
  (e.g. no-direct-average) — construction fails otherwise.
- VERIFIED dimensions require confidence 1.0.
- Discovery (`derive_dimension_candidates`) is deterministic over a bounded schema
  snapshot; candidates are runtime_discovered + UNKNOWN, never auto-trusted. High-cardinality
  columns are classified as identifiers with an explicit constraint note.
- UNKNOWN is never silently upgraded to VALID.

## Compatibility semantics

| Case | Result |
| --- | --- |
| Metric unregistered / inferred | UNKNOWN |
| Dimension unregistered | UNKNOWN (precondition: discover from data asset) |
| Dimension in metric.incompatible_dimensions | INVALID |
| Metric declares compatible_dimensions and dim not among them | INVALID |
| Metric has no declared compatibility contract | CONDITIONAL (verify fit first) |
| Granularity not supported | INVALID |
| Dimension belongs to another data asset | INVALID |
| Multi-dimension combination with unverified co-membership | CONDITIONAL (double-count check) |

## Drill-down semantics

Drill-down requires a relationship contract with status VERIFIED or DECLARED
(`ValidationStatus.drillable`). INFERRED or missing relationships return UNKNOWN with
"never fabricate a hierarchy" preconditions — exploration or clarification first. A valid
drill-down yields a `DrillDownPlan` (relationship id, status, filter propagation, grain).

## Bridge to the existing semantic layer

`langgraph_langchain/semantic/models.py` (WrenAI-style, prompt-injection only) remains the
declared source; the analysis-intelligence contracts add the deterministic enforcement layer
with real consumers (P2 intent, P3 constraint validation, P4 exploration).

## P1 Gate evidence

1. Built-in vs dynamic distinguishable (source field) — test passed.
2. Metric contract with ratio guards — tests passed.
3. Dimension contract with verification status — tests passed.
4. Hierarchy as machine-checkable relationship contracts — tests passed.
5. Drill-down validation conditions — VERIFIED allowed, INFERRED/missing blocked — tests passed.
6. Compatibility VALID/INVALID/CONDITIONAL/UNKNOWN executable — tests passed.
7. Unknown/ambiguous explicit behavior (never auto-VALID) — tests passed.
8. Discovery results carry provenance — tests passed.
9. LLM inference cannot become trusted — tests passed.
10. Real deterministic data case (candidates from schema snapshot) — tests passed.
11. No unnecessary infrastructure (one module, pure pydantic + stdlib).
12. **P1 Gate: PASS.**
