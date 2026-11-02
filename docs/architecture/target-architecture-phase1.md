# Phase 1 Target Architecture

## Principle

Phase 1 does not replace the current system. It converges the existing Runtime
around a general-purpose tabular analysis loop and adds the minimum database
capability required for the MVP.

## Target Architecture

```text
User / Streamlit / API clients
        ↓
API Layer
  - Sessions
  - Uploads
  - Plans
  - Runs
  - Artifacts
  - Reports
        ↓
Product Services
  - DataSource Registry
  - Schema/Quality Understanding
  - Plan Planner
  - Runtime Orchestrator
  - Evidence Service
  - Report Service
        ↓
Execution Layer
  - Structured Tools
  - Python/Pandas Sandbox
  - Read-only SQL Executor
  - Verification Policy
        ↓
Data Layer
  - FileDataSource
  - DatabaseDataSource
  - MemoryDataSource
  - QueryResultDataSource
  - DataAsset Registry
  - Cache
        ↓
Evidence / Audit Layer
  - Execution Records
  - Verification Results
  - Evidence Items
  - Findings
  - Report Claims
  - Run Snapshot / Package
```

## Phase 1 Module Boundaries

### 1. Data Source Layer

Keep:

```text
langgraph_langchain/data/sources.py
langgraph_langchain/data/assets.py
langgraph_langchain/data/cache.py
langgraph_langchain/data/sampling.py
```

Add:

```text
langgraph_langchain/data/database.py
```

Responsibilities:

- Connect to one configured relational source.
- Introspect schemas and tables.
- Execute read-only SQL.
- Return a governed `QueryResultDataSource` / `DataAsset`.
- Record connection metadata without exposing credentials.

First supported database:

- SQLite for deterministic CI tests.

Next adapters:

- PostgreSQL.
- MySQL.

### 2. Understanding Layer

Keep `eda_profile` and extend it into a reusable service:

```text
langgraph_langchain/runtime/understanding.py
```

Responsibilities:

- Schema snapshot.
- Column profiles.
- Missing values.
- Duplicates.
- Outliers.
- Cardinality.
- Basic quality warnings.
- Dataset summary for planner.

### 3. Planner Layer

Add:

```text
langgraph_langchain/runtime/generic_planner.py
```

Responsibilities:

- Convert user question + dataset summary + available tools into an
  `AnalysisPlan`.
- Use deterministic templates for simple tasks.
- Use LLM only to choose parameters/methods, not to invent values.
- Validate the plan against available tools/assets/columns.
- Emit plan metadata for audit.

### 4. Runtime Layer

Use existing:

```text
langgraph_langchain/runtime/context.py
langgraph_langchain/runtime/scheduler.py
langgraph_langchain/runtime/runner.py
langgraph_langchain/runtime/graph.py
```

Goal:

- Make Runtime V2 the controlled default for file/database analysis.
- Keep legacy ReAct as an explicit emergency path, not silent fallback.

### 5. Execution Layer

Keep:

```text
langgraph_langchain/execution/structured_executor.py
langgraph_langchain/execution/python_executor.py
langgraph_langchain/execution/python_task_executor.py
```

Add:

```text
langgraph_langchain/tools/tool_query_database.py
```

Requirements:

- SQL is read-only.
- Statement allowlist begins with `SELECT` or adapter-supported read syntax.
- Statement timeout and row limit.
- Query text, connection id, and result artifact are recorded.
- Credentials are not exposed to the Python sandbox.

### 6. Evidence Layer

Keep existing evidence architecture. Phase 1 requirements:

- Every SQL execution creates an execution record.
- Every result artifact gets deterministic verification.
- Evidence links:
  - source connection metadata,
  - SQL text,
  - result artifact,
  - execution id,
  - verification ids.
- Findings cannot cite unverified executions.

### 7. Report Layer

Keep `finish_report` and report rebuild. Phase 1 report must include:

- Data context.
- Metric definitions.
- Data quality notes.
- Key findings.
- Tables/charts.
- Evidence references.
- Limitations.
- SQL/pandas reproducibility notes.

## Migration Strategy

| Current Capability | Phase 1 Treatment |
| --- | --- |
| FileDataSource | Keep |
| QueryResultDataSource | Keep; feed from new SQL executor |
| Runtime V2 | Promote behind a tested gate |
| Legacy ReAct | Keep only as explicit emergency path |
| Financial Domain | Freeze and do not import in new generic modules |
| Streamlit UI | Keep; adapt calls if needed |
| `langgraph_agent.py` monolith | Extract only touched logic |

## Anti-goals

- No new financial semantics.
- No macro data collection.
- No multi-agent workflow.
- No new UI framework.
- No distributed execution.
- No generic SQLAlchemy ORM models for user data.
