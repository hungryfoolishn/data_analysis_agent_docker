# Analysis Intent Understanding (P2)

Entry: `langgraph_langchain/analysis_intelligence/intent.py`
Tests: `tests/test_analysis_intelligence_intent.py` (17 golden cases, all passing)

## Design

- **Intent vs Plan separation**: `AnalysisIntent` is an immutable record of the user goal;
  the planner receives an intent reference and cannot rewrite `user_goal`.
- **Deterministic resolution** (no LLM at this layer): Chinese-aware substring matching
  against the P1 `SemanticRegistry` (longest-match first, question-order preserved),
  deterministic type patterns, ranking/TopN parsing, relative-time parsing, explicit
  filter extraction, collection-vs-single-object constraints.
- **Per-field provenance**: every resolved field records
  EXPLICIT / RESOLVED / INFERRED / ASSUMED / UNKNOWN.

## Completeness states

| Status | Trigger |
| --- | --- |
| READY | metrics + dimensions resolved, no assumptions, no ambiguity |
| READY_WITH_ASSUMPTIONS | resolvable but recorded assumptions (e.g. ranking without N → full ranking; descending order assumed) |
| NEEDS_EXPLORATION | unregistered dimension candidate exists in schema; trend without a time column; drill-down terms unresolved-but-explorable |
| NEEDS_CLARIFICATION | unknown metric; ambiguous dimension term (not in registry nor schema) |
| UNSUPPORTED | unsupported analysis type (e.g. forecast) or INVALID metric-dimension combination |

Unknown is never silently promoted: an unregistered-but-schema-present dimension becomes
a `candidate_in_data` with INFERRED provenance and NEEDS_EXPLORATION; a fully unknown term
becomes NEEDS_CLARIFICATION with the ambiguous term recorded.

## Analysis types

Detection order: forecast → drill_down → ranking → trend → comparison → anomaly →
correlation → contribution → distribution → aggregation fallback. `SUPPORTED_ANALYSIS_TYPES`
whitelist guarantees unsupported requests (e.g. forecast) yield UNSUPPORTED instead of
a silently degraded plan.

## P2 Gate — golden case coverage (all semantic assertions)

单指标 ✓ 多指标 ✓ metric+dimension ✓ 多维(组合成员) ✓ ranking+TopN ✓ ranking-without-N
assumption ✓ trend-needs-time ✓ trend-with-granularity ✓ relative-time provenance ✓
drill-down verified relationship ✓ unknown metric ✓ ambiguous dimension ✓ schema
dimension candidate ✓ invalid metric-dimension → UNSUPPORTED ✓ missing time ✓ filters ✓
collection vs single object ✓ multi-step goal (drill-down) ✓ unsupported request ✓

**P2 Gate: PASS.**
