# B11 Final Acceptance — Research-Oriented Multi-Agent System

**Date:** 2026-11-18
**Branch:** `codex/phase1-m2.4-sqlite-schema`
**Final HEAD:** `c2c559d`
**Environment:** docker-host, conda `analysis`

## 1. Scope Delivered

| Stage | Commit | Content |
|---|---|---|
| B11.0 | `cd36d67` | Runtime readiness & unlock audit — B11 = UNLOCKED |
| B11.1 | `5182fc5` + `f40492c` | Planner/Executor/Reviewer contracts, `MultiAgentOrchestrator`, `SingleAgentRunner` baseline, `compare_benchmark` |
| B11.2 | `0f36e92` | `ResearchPlanningAgent` — multi-step dependency-aware plans; `ResearchStep.to_runtime_step()` / `ResearchPlan.to_runtime_plan()` map to Runtime V2 `RuntimePlanStep` |
| B11.3 | `0f36e92` | `DataAnalystAgent`, `ReportWriterAgent`, `QualityAuditorAgent` specialized roles |
| B11.4 | `0f36e92` | `ResearchFinding` / `ResearchSynthesis` evidence contract with ordered evidence dedup |
| B11.5 | `0f36e92` | `ResearchOrchestrator` — bounded retry, budget enforcement, conflict resolution via auditor review loop; `step_executor` plumbed through to analyst (regression-tested) |
| B11.6 | `c2c559d` | `ResearchBenchmarkRunner` — fair single vs multi comparison over B6 golden data; results JSON + evaluation report |
| B11 Final | this doc | Full regression + freeze assessment |

## 2. Test Evidence

All tests executed on docker-host (`/tmp/b11-test`, conda `analysis`):

| Suite | Result |
|---|---|
| `tests/test_b11_1_multi_agent.py` + `tests/test_b11_2_research.py` + `tests/test_b11_6_benchmark.py` | **37 passed / 0 failed** |
| Full regression at `c2c559d` | **1715 passed / 2 failed / 13 skipped** |

The 2 full-regression failures are the known pre-existing baseline failures
(`tests/test_dashboard.py::test_individual_metrics_endpoints`,
`tests/test_dashboard.py::test_dashboard_display_format`) — identical node ids
to the accepted B10.11 baseline. The 9 `test_reliability.py` baseline failures
reported in earlier environments did not reproduce in this run; no new failure
was introduced by B11.

## 3. Benchmark Result (B11.6)

- 48 verifiable golden cases: single 1.00 / multi 1.00 finding accuracy;
  evidence validity 1.00 both; numeric no-regression gate PASS.
- Fault injection: multi-agent structured uncertainty disclosure 1.0 vs
  single-agent silent failure 0.0 (`review_decision=reject` with auditable
  issues).
- Latency overhead ratio ≈ 1.01; no LLM/token cost in the deterministic
  benchmark loop.
- Known golden-data ground-truth inconsistencies documented and excluded
  (2 trend cases, 12 change_pct metrics) — flagged as B6 golden-data P2.

Full detail: `docs/architecture/benchmarks/b11.6-multi-agent-evaluation.md`.

## 4. Issues

| Level | Finding | Disposition |
|---|---|---|
| P2 | B6 golden JSON Q2 trend ground truth (0.0) inconsistent with frozen dataset content | Documented + excluded from benchmark; golden-data fix deferred (not B11 scope) |
| P2 | Comprehensive-case `revenue_change_pct` expectations lack `compare_period` semantics | Documented + excluded from scoring |
| P2 | Benchmark quality improvement limited to uncertainty disclosure on clean data | Honest limitation recorded in B11.6 report §5 |

No P0, no P1.

## 5. Freeze Decision

- B11.0 PASS, B11.1 PASS, B11.2–B11.5 PASS (37 tests), B11.6 PASS under
  documented scope, B11 Final regression clean (0 new failures).
- Multi-agent quality: no numeric regression; structured failure disclosure
  improvement demonstrated; latency overhead negligible.
- All B11 stages are committed and pushed; local HEAD == remote HEAD.

**B11: PASS / FROZEN**

Next: B12 planning can proceed; the golden-data P2 items should be tracked as
a follow-up data-quality task outside B11.
