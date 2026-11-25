# Dynamic Data Exploration & Feedback (P4)

Entry: `langgraph_langchain/analysis_intelligence/exploration.py`
Tests: `tests/test_analysis_intelligence_exploration.py` (8 passed)

## Design

- **Question-driven, not pipeline-driven**: `ExplorationDecider.next_question` derives the
  next exploration from the intent actual unknowns (unresolved dimension term → dimension
  candidate exploration; trend without time column → time-column exploration; otherwise
  plan directly).
- **Bounded data access**: `SchemaExplorationEngine` consumes only a `SchemaProfile`
  (bounded schema snapshot already obtained through the governed data layer via
  `scan_schema`). The engine itself never reads raw data — no new data-access path.
- **Structured results**: `ExplorationResult` carries findings, `DimensionContract`
  candidates, time column, evidence refs (schema anchors), confidence, limitations, and a
  `decision` (BUILD_PLAN / EXPLORE_MORE / VALIDATE_CANDIDATE / ASK_USER / STOP).
- **Real fold-back**: `apply_result` mutates the live intent — unresolved dimension gets
  its entity_id from the verified candidate (name-matched), trend gets its time column,
  status is recomputed. State changes are recorded in `intent.status_reasons` with the
  exploration id.

## Divergence proof (P4 gate: 2+ different results → different paths)

| Scenario | Exploration result | Path |
| --- | --- | --- |
| Dimension candidate exists (card 12) | VALIDATE_CANDIDATE | fold back → READY → group_by plan |
| Schema has no categorical candidate | ASK_USER | intent → NEEDS_CLARIFICATION |
| Time column exists (datetime dtype) | BUILD_PLAN | trend plan with the discovered column |
| No temporal column | ASK_USER | clarification instead of plan |

Additional proven behavior: a discovered dimension CANNOT silently join a metric that
explicitly declares incompatible boundaries — planning is blocked end-to-end
(`test_explored_dimension_respects_declared_metric_boundary`), while a metric without a
declared allowlist proceeds with recorded preconditions.

## Efficiency & termination

- `ExplorationLedger`: MAX_EXPLORATIONS=5 budget; identical question re-execution raises
  `redundant exploration`; beyond-budget distinct exploration raises `budget exhausted`.
- Termination states: plan buildable, ASK_USER, budget exhausted, STOP (unknown target).

## P4 Gate evidence

1. Agent explores proactively ✓ (next_question from intent unknowns)
2. Task-driven ✓ 3. Results influence next step ✓ (fold-back mutations)
4. Two different results → two different paths ✓ (4 divergence scenarios tested)
5. Dynamic dimension discovery verifiable ✓ (cardinality-gated candidates)
6. Exploration results tracked ✓ (ledger + exploration_id + evidence refs)
7. No fixed pipeline ✓ (decision depends on result)
8. Bounded ✓ (MAX_EXPLORATIONS + redundancy detection)
9. No security bypass ✓ (profile-only consumption; V1.1 data boundary untouched)
10. Tests repeatable ✓ — **P4 Gate: PASS.**
