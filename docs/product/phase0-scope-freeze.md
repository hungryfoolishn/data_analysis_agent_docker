# Phase 0 Scope Freeze

## Baseline

- Baseline commit: `5314055`
- Branch: `codex/phase0-product-architecture-baseline`
- Phase 0 date: 2026-11-02
- Test evidence: `PYTHONPATH=. pytest -q`
  - Result: `930 passed, 11 warnings, 4 subtests passed`
  - Environment: docker-host / Conda `analysis` / Python 3.10

## Phase 0 Goal

Converge the product direction, audit the existing code architecture, classify
modules, and produce an executable plan for the Phase 1 general analysis MVP.

Phase 0 is not a feature milestone. It should not be measured by adding more
financial tests or rules.

## Paused Development

The following work is paused after Phase 0 until a later Domain Pack phase:

- New financial metric semantics.
- New year-over-year, quarter-over-quarter, period-basis, or financial report
  interpretation rules.
- New financial data collectors or public API adapters.
- Macro analysis Domain Pack implementation.
- Multi-agent financial research workflows.
- New R&D-domain prompt or template expansion.

## Allowed Changes During Phase 0

Only the following are allowed:

1. Documentation-only architecture and product work.
2. Fixes for reproducible P0 issues: execution safety, data corruption, system
   unavailability.
3. Fixes for reproducible P1 issues that block Phase 1.
4. Small, test-backed clarifications required to keep the existing suite green.
5. Module classification and issue registration.

## Explicit Non-Goals For Phase 0

- Rewriting `langgraph_langchain/langgraph_agent.py`.
- Rewriting the Streamlit UI.
- Replacing LangGraph or Runtime V2.
- Moving financial code into new packages.
- Implementing database adapters.
- Implementing the Phase 1 MVP.
- Adding macro or multi-agent capabilities.
- Fixing every P2/P3 financial or UX edge case.

## Change Rule

Every proposed change during Phase 0 must answer:

1. Which user scenario does it support?
2. Does it block Phase 1?
3. What happens if it is not implemented now?
4. What files or modules does it touch?
5. What test or artifact proves completion?

If a change is not P0/P1 and does not support Phase 1 planning, it belongs in
the backlog, not the current milestone.
