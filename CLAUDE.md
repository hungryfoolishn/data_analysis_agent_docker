# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeepAnalyze is a data analysis agent system built on the LangGraph/LangChain stack. It provides automated data analysis through a Streamlit web UI, with streaming SSE responses and session-based workspace management.

## Architecture

### Current Stack (LangGraph Implementation)

```
Streamlit WebUI (port 8501)
    ↓
FastAPI Backend (port 8888) - OpenAI-compatible API
    ↓
LangGraph ReAct Agent
    ↓
Tools: load_data → eda_profile → python_repl → finish_report
    ↓
workspace/<session_id>/ (artifacts, logs, reports)
```

**Key components:**
- `langgraph_langchain/api_server_langgraph.py` - FastAPI server with SSE streaming
- `langgraph_langchain/langgraph_agent.py` - LangGraph agent, tools, runtime constraints (148KB, core logic)
- `langgraph_langchain/schemas.py` - Pydantic models for stages, findings, evidence, failures
- `webui/app.py` - Streamlit frontend

**Analysis flow:**
1. User uploads data file to session workspace
2. Agent loads data and runs EDA profiling
3. Multi-step analysis with `python_repl` (enforced small steps, bilingual markers)
4. Final report generation with `finish_report` (one-time protection)

### Runtime Guardrails

The `python_repl` tool enforces:
- Max 50 lines per step (`_MAX_PYTHON_REPL_LINES`)
- Required step markers (bilingual Chinese/English):
  - "step objective" / "步骤目标"
  - "method" / "方法"
  - "key results" / "关键结果"
  - "suggested next step" / "建议下一步"
- Max 24 agent steps (`_MAX_AGENT_STEPS`)
- Max 3 consecutive Python errors (`_MAX_CONSECUTIVE_PYTHON_ERRORS`)
- 60s timeout per execution (`_CODE_TIMEOUT`)

### R&D Efficiency Domain

Specialized validators and metric library for R&D team analysis:
- `rd_efficiency_domain.py` - metric definitions (velocity, cycle time, defect rate)
- `rd_metric_library.py` - calculation validators and interpretation helpers
- `rd_validators.py` - domain-specific validation (sprint data, PR data, deployment data)
- `rd_templates.py` - analysis templates for common R&D scenarios

### Tracing & Observability

- `tracing.py` - trace context management for request tracking
- `structured_logging.py` - structured logging with trace correlation
- `lineage.py` - data lineage tracking
- `workspace_manager.py` - session workspace lifecycle management
- `stability_metrics.py` - metrics tracker for reliability monitoring
- `recovery.py` - recovery executor for failure handling

### P1–P4 Infrastructure

- `prompts/prompt_builder.py` - dynamic prompt assembly from modular .md sections (P1)
- `prompts/sections/` - 8 editable .md prompt sections (P1)
- `error_classifier.py` - API error taxonomy with 11 types + recovery hints (P2)
- `retry_utils.py` - jittered exponential backoff for retries (P2)
- `session_persistence.py` - session state serialization for resume (P3)
- `tools/tool_delegate.py` - sub-task delegation tool for parallel analysis (P4)
- `tools/registry.py` - tool registry with factory pattern + auto-discovery
- `tools/_shared.py` - shared helpers (path safety, step validation, evidence regex)
- `skills_loader.py` - progressive skills disclosure framework
- `skills/` - 4 built-in analysis skills (eda, trend, anomaly, attribution)

## Development Commands

### Environment Setup

```bash
# Using conda (recommended)
conda env create -f environment.yml
conda activate smolagents

# Using pip
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r langgraph_langchain/requirements-langgraph.txt
```

### Running Services

```bash
# Start LangGraph backend (recommended)
python -m langgraph_langchain.api_server_langgraph
# Listens on http://localhost:8888

# Start Streamlit frontend (new terminal)
streamlit run webui/app.py
# Opens at http://localhost:8501

# Or use Makefile shortcuts
make server    # Start backend
make frontend  # Start frontend
make dev       # Start both
```

### Testing

```bash
# Run all tests (331 tests collected)
pytest

# Run specific test suites
pytest tests/test_validator_e2e.py
pytest tests/test_tracing.py
pytest tests/test_state_machine_enforcement.py
pytest langgraph_langchain/test_reliability.py

# Test API endpoints
make test-api
# Or manually:
curl http://localhost:8888/health
curl http://localhost:8888/workspace/files
```

### Cleanup

```bash
make clean      # Remove __pycache__, *.pyc, .pytest_cache
make clean-all  # Also remove workspace/*
```

## Configuration

### Environment Variables (.env)

```bash
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL_ID=deepseek-chat
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
MAX_CONCURRENT_AGENTS=3
SESSION_TTL_HOURS=24
```

### Frontend Config (webui/config.py)

```python
API_BASE_URL = "http://localhost:8888"
FILE_SERVER_BASE = "http://116.148.124.7:8888"
DEFAULT_MODEL = "deepseek-chat"
```

## API Endpoints

The backend provides OpenAI-compatible endpoints:

- `POST /v1/chat/completions` - Streaming/non-streaming analysis (SSE)
- `POST /v1/files` - OpenAI-style file upload
- `POST /workspace/upload` - Session-aware file upload
- `GET /workspace/files?session_id=<id>` - List session files & artifacts
- `GET /workspace/files/{filename:path}` - Download file
- `DELETE /sessions/{session_id}?purge=true` - Delete session
- `POST /sessions/{session_id}/cancel` - Cancel running analysis
- `POST /sessions/{session_id}/resume` - Resume interrupted analysis (P3)
- `GET /sessions/{session_id}/resumable` - Check if session has resumable state (P3)
- `GET /health` - Health check with concurrency stats

## Key Implementation Details

### Session Management

- Sessions stored in `workspace/.sessions.json`
- Each session has isolated workspace: `workspace/<session_id>/`
- Session TTL: 24 hours (configurable via `SESSION_TTL_HOURS`)
- Cancellation support via `_ACTIVE_CANCELS` event registry
- Concurrency control via semaphore (`MAX_CONCURRENT_AGENTS`)

### Analysis Stages (state_machine.py)

```python
AnalysisStage = Literal[
    "planning",
    "data_loading",
    "eda",
    "deep_analysis",
    "reporting",
    "completed",
    "failed"
]
```

### Failure Handling

Structured failure codes with recovery policies:
- `missing_data_file`, `session_not_found`, `session_expired` - user action required
- `python_execution_error`, `max_steps_exceeded`, `report_rejected` - retry with narrower scope
- `cancelled` - retry same scope
- See `_failure_policy()` in `api_server_langgraph.py`

### Helper Functions (Pre-loaded in python_repl)

```python
save_fig(filename)                    # Save matplotlib figure
fix_chinese()                         # Fix Chinese font rendering
profile_dimension(df, dim, metric)    # Dimension profiling
compare_segments(df, segments)        # Segment comparison
time_trend(df, time_col, metric)      # Time series analysis
detect_anomalies(series)              # Anomaly detection
explain_metric_change(before, after)  # Change attribution
```

## Testing Strategy

The test suite (315 tests) covers:
- **E2E validation**: `test_validator_e2e.py` - full analysis flow with real datasets
- **R&D domain**: `test_week4_rd_domain.py`, `test_rd_validator_integration.py`
- **Tracing**: `test_tracing.py` - trace context propagation
- **State machine**: `test_state_machine_enforcement.py` - stage transitions
- **Error handling**: `test_error_messages.py`, `test_api_error_integration.py`
- **Recovery**: `test_recovery_strategy.py`
- **Workspace**: `test_workspace_manager.py`
- **Reliability**: `langgraph_langchain/test_reliability.py` - stress testing

Representative E2E scenarios (all passing):
- `grouped_sales` - grouped analysis with segment comparison
- `time_series_anomaly` - time series with anomaly detection
- `quality_issues` - data quality analysis

## Code Conventions

### Bilingual Support

The system supports both Chinese and English throughout:
- System prompts default to Chinese analysis output
- Step markers accept both languages
- Error messages formatted bilingually via `error_messages.py`
- Frontend UI in Chinese

### Structured Output

Tools return structured data:
- `Finding` - analysis findings with evidence
- `MetricDefinition` - metric definitions with calculation methods
- `EvidenceItem` - evidence with source fields, artifacts, stats
- `StageResult` - stage completion with status and artifacts

### Logging

Use `StructuredLogger` for trace-aware logging:
```python
from langgraph_langchain.structured_logging import StructuredLogger
logger = StructuredLogger(__name__)
logger.info("message", extra={"key": "value"})
```

## Important Notes

- **Backend priority**: Focus development effort on backend analysis capability over UI polish (per user preference)
- **Chinese fonts**: matplotlib Chinese rendering fixed via `fix_chinese()` helper
- **Streaming**: SSE responses include `Cache-Control: no-cache` and `X-Accel-Buffering: no`
- **Output truncation**: Tool outputs truncated to 3000 chars (`_MAX_OUTPUT_LEN`) to preserve context
- **One-time report**: `finish_report` has protection against multiple calls per session
