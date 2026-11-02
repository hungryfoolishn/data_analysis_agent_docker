# Phase 0 Issue Register

Issues are based on code inspection and the current test suite at commit
`5314055`. No new feature work is performed in Phase 0.

## Summary

| Level | Count | Meaning |
| --- | --- | --- |
| P0 | 0 | Immediate safety/data-loss/unavailable issue found |
| P1 | 5 | Blocks Phase 1 or makes its acceptance unreliable |
| P2 | 4 | Important, but should not interrupt Phase 0 |
| P3 | 2 | Future improvement |

## P1-001 — Runtime V2 is not enabled by default

- **Level:** P1.
- **Evidence:**
  - `langgraph_langchain/config.py`: `RUNTIME_V2_ENABLED` defaults to false.
  - `langgraph_langchain/langgraph_agent.py`: default `run_analysis_stream()`
    only enters `_run_runtime_v2_graph_stream()` when that flag is true.
- **Impact:** The tested structured Runtime is not the default user path. The
  default path is still the legacy ReAct flow.
- **Why it matters:** Phase 1 depends on generic plans, deterministic execution,
  evidence, and traceability. Keeping Runtime V2 optional makes the desired
  architecture non-default.
- **Minimal Phase 1 fix:** Add a Runtime V2 default-path gate behind tests, then
  enable it for Phase 1 file analysis after golden acceptance.
- **Acceptance:** File-analysis golden questions execute through Runtime V2
  without falling back to legacy ReAct.

## P1-002 — No relational database adapter or read-only SQL execution

- **Level:** P1.
- **Evidence:**
  - `langgraph_langchain/data/sources.py` defines `FileDataSource`,
    `MemoryDataSource`, and `QueryResultDataSource`.
  - `DataSource` explicitly states SQL support is intentionally excluded from
    the current P0 contract.
  - No PostgreSQL/MySQL/SQLite adapter or connection manager exists in
    `langgraph_langchain/data`.
- **Impact:** Phase 1 requires relational database analysis, but the current
  data layer cannot connect to a database.
- **Minimal fix:** Add a `DatabaseDataSource` plus a read-only SQL execution
  tool. Start with SQLite as the integration gate, then add PostgreSQL or MySQL.
- **Acceptance:**
  - Schema scan returns tables/columns.
  - Query result becomes a runtime `DataAsset`.
  - Read-only SQL cannot mutate data.
  - Query evidence links query, result artifact, and source connection metadata.

## P1-003 — Generic structured planning is not wired into the default chat path

- **Level:** P1.
- **Evidence:**
  - `_run_runtime_v2_graph_stream()` returns fallback if
    `runtime.run.plan` is absent.
  - `AnalysisRuntime.propose_default_plan()` creates only a fixed five-step
    compatibility plan.
  - `/workspace/upload` does not create a structured plan.
  - `/v1/chat/completions` does not automatically create or propose a plan.
- **Impact:** A normal user upload plus question does not reliably enter the
  structured Runtime path.
- **Minimal fix:** In Phase 1, add a planner step that creates a data-aware
  `AnalysisPlan` from schema/profile plus user question, then propose it before
  execution.
- **Acceptance:** Upload + question produces an inspectable plan for all Phase 1
  golden scenarios.

## P1-004 — Agent/API/Runtime code is too monolithic for safe extension

- **Level:** P1.
- **Evidence:**
  - `langgraph_langchain/langgraph_agent.py`: 2,099 lines.
  - `langgraph_langchain/api_server_langgraph.py`: 1,926 lines.
  - `langgraph_langchain/runtime/context.py`: 1,230 lines.
  - `langgraph_langchain/tools/tool_finish_report.py`: 539 lines.
- **Impact:** File analysis, database analysis, planning, evidence, recovery,
  session management, and financial bridges are difficult to change safely.
- **Minimal fix:** Do not rewrite in Phase 0. In Phase 1, extract only what is
  touched by database/planner work, with tests:
  - session/workspace service,
  - structured planner,
  - report validator,
  - runtime bridge,
  - API routers.
- **Acceptance:** No behavior change except explicit Phase 1 features; existing
  tests pass and new extracted modules have focused tests.

## P1-005 — No database golden dataset or end-to-end acceptance

- **Level:** P1.
- **Evidence:**
  - Existing Runtime V2 e2e tests use files or in-memory fixtures.
  - No test executes a relational database analysis through API or Runtime V2.
- **Impact:** Phase 1 acceptance cannot be demonstrated yet.
- **Minimal fix:** Create a Phase 1 golden dataset plan and database fixture.
- **Acceptance:** Golden questions cover upload and database paths, and CI runs
  the database e2e test.

## P2-001 — Financial Domain code is physically inside generic Runtime

- **Evidence:** `langgraph_langchain/runtime/financial` and
  `langgraph_langchain/runtime/financial_agent`.
- **Impact:** Future generic Runtime changes may accidentally depend on finance.
- **Decision:** Freeze now; migrate to a Domain Pack in Phase 4.

## P2-002 — Report and evidence behavior is spread across several modules

- **Evidence:** `tool_finish_report.py`, `runtime/report_validator.py`,
  `runtime/quality.py`, `runtime/report_claims.py`, `runtime/report_rebuild.py`,
  `evidence/collector.py`.
- **Impact:** Correct but difficult to reason about.
- **Decision:** Extract only when Phase 1 requires changes.

## P2-003 — Full suite has warnings but no warning-quality gate

- **Evidence:** Full pytest produces 11 warnings on Python 3.10 and more on
  Python 3.14, including Pydantic serializer warnings.
- **Impact:** Not blocking, but warnings hide future regressions.
- **Decision:** Add warning triage in Phase 1 hardening; do not fix now.

## P2-004 — Package packaging/config is split across multiple files

- **Evidence:** `requirements.txt`, `requirements-dev.txt`,
  `langgraph_langchain/requirements-langgraph.txt`, no `pyproject.toml`.
- **Impact:** Dependency reproducibility is weaker than a single project config.
- **Decision:** Evaluate in Phase 1 hardening; not required for audit.

## P3-001 — Historical Runtime docs are version-heavy

- **Impact:** New contributors may mistake historical docs for current truth.
- **Decision:** Keep, but new product/architecture docs are authoritative.

## P3-002 — Some tests live inside the package

- **Evidence:** `langgraph_langchain/test_reliability.py`,
  `langgraph_langchain/test_findings.py`.
- **Decision:** Move in a later cleanup.

## Immediate Phase 1 Blocking List

The following must be solved before Phase 1 can be declared complete:

1. P1-001: promote Runtime V2 as the controlled default path.
2. P1-002: implement database source and read-only SQL execution.
3. P1-003: implement generic data-aware planning.
4. P1-005: run golden file/database end-to-end acceptance.

P1-004 should be incrementally reduced but is not required to be fully solved.
