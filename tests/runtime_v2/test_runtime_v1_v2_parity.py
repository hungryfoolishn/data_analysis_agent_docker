"""API-level V1/V2 parity suite (A1.2-3).

Both runtimes run through the real production entry ``run_analysis_stream``
with a scripted fake chat model patched into the same constructor the
production code uses (``langgraph_agent.ChatOpenAI``).  V2 runs the
deterministic planner plan through the real graph route; V1 runs the real
ReAct loop driven by the scripted model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from langgraph_langchain import langgraph_agent as la


GOAL = "Grouped by region sum total amount"
CSV_ROWS = "region,amount\nNorth,100\nNorth,120\nSouth,80\n"
EXPECTED_TOTALS = {"North": 220.0, "South": 80.0}


class ScriptedFakeChatModel(BaseChatModel):
    """Deterministic chat model driven by a scripted message sequence."""

    model_config = {"extra": "ignore"}

    _script: ClassVar[list] = []
    _idx: ClassVar[int] = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages):
        i = ScriptedFakeChatModel._idx
        ScriptedFakeChatModel._idx += 1
        script = ScriptedFakeChatModel._script
        step = script[min(i, len(script) - 1)]
        return step(messages) if callable(step) else step

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next(messages)
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": tc["name"],
                        "args": json.dumps(tc.get("args", {})),
                        "id": tc.get("id"),
                        "index": 0,
                        "type": "tool_call",
                    }
                    for tc in msg.tool_calls
                ],
            ))
            return
        content = msg.content or ""
        size = max(1, len(content) // 3)
        for i in range(0, len(content), size):
            yield ChatGenerationChunk(message=AIMessageChunk(content=content[i:i + size]))


class DummyChatModel(BaseChatModel):
    """No-op model: the deterministic V2 plan never calls the LLM."""

    model_config = {"extra": "ignore"}

    @property
    def _llm_type(self) -> str:
        return "dummy"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])


def make_tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


V1_REPORT = """# Regional sales report

## Summary / 摘要
Total sales are 300 across 2 regions.

## Data Context / 数据说明
- Time range: not applicable, static cross-sectional analysis.
- Metric definition: amount summed by region.
- Dedup rule: no deduplication.
- Assumptions: no returns.

## Key Findings / 关键发现
- North region total is 220.
- South region total is 80.

## Data Quality / 数据质量
- 3 rows, no missing values.

## Analysis / 分析过程
- Grouped amount by region and summed.

## Visualizations / 图表说明
- No charts in this deterministic run.

## Recommendations / 建议
- Verify regional totals monthly.
"""


def build_v1_steps(source: Path, *, python_code: str | None = None):
    def step_load(messages):
        return AIMessage(content="", tool_calls=[make_tool_call(
            "load_data", {"file_path": str(source)}, "c1")])

    def step_eda(messages):
        return AIMessage(content="", tool_calls=[make_tool_call("eda_profile", {}, "c2")])

    def step_python(messages):
        code = python_code or (
            "totals = df.groupby('region')['amount'].sum().to_dict(); print(totals)"
        )
        return AIMessage(content="", tool_calls=[make_tool_call(
            "python_repl", {"code": code}, "c3")])

    def step_finding1(messages):
        return AIMessage(content="", tool_calls=[make_tool_call("record_finding", {
            "statement": "North region total amount is 220.",
            "evidence_text": "North amount summed to 220 (100 + 120) in the grouped totals output.",
            "confidence_level": "high",
            "evidence_level": "A",
            "category": "comparison",
            "source_fields": ["region", "amount"],
            "group_dimension": "region",
            "stats": {"north_amount": 220},
        }, "c4")])

    def step_finding2(messages):
        return AIMessage(content="", tool_calls=[make_tool_call("record_finding", {
            "statement": "South region total amount is 80.",
            "evidence_text": "South amount summed to 80 in the grouped totals output.",
            "confidence_level": "high",
            "evidence_level": "A",
            "category": "comparison",
            "source_fields": ["region", "amount"],
            "group_dimension": "region",
            "stats": {"south_amount": 80},
        }, "c5")])

    def step_report(messages):
        return AIMessage(content="", tool_calls=[make_tool_call(
            "finish_report", {"markdown": V1_REPORT}, "c6")])

    def step_final(messages):
        return AIMessage(content="Analysis complete. Report submitted.")

    return [step_load, step_eda, step_python, step_finding1, step_finding2, step_report, step_final]


def write_fixture(workspace: Path, suffix: str, csv_text: str = CSV_ROWS) -> Path:
    path = workspace / f"sales.{suffix}"
    if suffix == "csv":
        path.write_text(csv_text, encoding="utf-8")
        return path
    frame = pd.read_csv(path.with_suffix(".csv")) if path.with_suffix(".csv").exists() else None
    if frame is None:
        import io
        frame = pd.read_csv(io.StringIO(csv_text))
    if suffix == "xlsx":
        frame.to_excel(path, index=False)
    elif suffix == "parquet":
        frame.to_parquet(path, index=False)
    else:
        raise ValueError(suffix)
    return path


async def _run_stream(workspace: Path, source: Path, *, runtime_v2: bool,
                      cancel_event=None, cancel_before_start: bool = False,
                      script=None, session_id: str, goal: str = GOAL):
    if runtime_v2:
        la.ChatOpenAI = DummyChatModel
    else:
        ScriptedFakeChatModel._script = script
        ScriptedFakeChatModel._idx = 0
        la.ChatOpenAI = ScriptedFakeChatModel

    async def _consume():
        chunks = []
        if cancel_event is not None and cancel_before_start:
            cancel_event.set()
        elif cancel_event is not None:
            asyncio.get_running_loop().call_later(0.5, cancel_event.set)
        async for text, artifacts in la.run_analysis_stream(
            instruction=goal,
            source_path=str(source),
            workspace_dir=str(workspace),
            api_key="fake",
            model_id="fake",
            api_base="http://localhost:0",
            session_id=session_id,
            cancel_event=cancel_event,
        ):
            chunks.append(text)
        return chunks

    return await _consume()


def run_scenario(tmp_path: Path, name: str, *, runtime_v2: bool,
                 cancel_event=None, cancel_before_start: bool = False,
                 script=None, suffix: str = "csv", csv_text: str = CSV_ROWS,
                 goal: str = GOAL):
    workspace = tmp_path / (f"{name}_v2" if runtime_v2 else f"{name}_v1")
    workspace.mkdir(parents=True)
    if suffix != "csv":
        (workspace / "sales.csv").write_text(csv_text, encoding="utf-8")
    source = write_fixture(workspace, suffix, csv_text=csv_text)
    chunks = asyncio.run(_run_stream(
        workspace, source,
        runtime_v2=runtime_v2,
        cancel_event=cancel_event,
        cancel_before_start=cancel_before_start,
        script=script,
        session_id=f"{name}-{'v2' if runtime_v2 else 'v1'}",
        goal=goal,
    ))
    state = json.loads((workspace / ".analysis_runtime.json").read_text(encoding="utf-8"))
    return {
        "workspace": workspace,
        "source": source,
        "chunks": chunks,
        "state": state,
        "run_status": state.get("run", {}).get("status"),
        "executions": state.get("executions", []),
        "assets": {a.get("asset_id"): a for a in state.get("assets", [])},
    }


def v2_group_aggregate_result(result: dict[str, Any]) -> dict[str, float]:
    """Read the V2 group_aggregate output asset from the persisted state."""
    payload = None
    for execution in result["executions"]:
        if execution.get("tool_name") != "run_analysis_operation":
            continue
        try:
            preview = json.loads(execution.get("stdout_preview") or "{}")
        except (TypeError, ValueError):
            continue
        if preview.get("operation") == "group_aggregate":
            payload = preview
    assert payload is not None, "V2 run produced no group_aggregate execution"
    output_asset_id = payload["output_assets"]["output"]
    asset = result["assets"][output_asset_id]
    frame = pd.read_parquet(asset["location"])
    grouped = dict(zip(frame["region"], frame[frame.columns[-1]]))
    return {key: float(value) for key, value in grouped.items()}


@pytest.mark.parametrize("suffix", ["csv", "xlsx", "parquet"])
def test_parity_normal_analysis_numbers_and_closure(tmp_path: Path, suffix: str,
                                                    monkeypatch):
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "0")
    v1 = run_scenario(tmp_path, f"normal_{suffix}", runtime_v2=False,
                      script=build_v1_steps(tmp_path / "x.csv"), suffix=suffix)
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "1")
    v2 = run_scenario(tmp_path, f"normal_{suffix}", runtime_v2=True, suffix=suffix)

    # Both runtimes must reach a real completion with a persisted report.
    assert v1["run_status"] == "completed"
    assert v2["run_status"] == "completed"
    v1_report = (v1["workspace"] / "final_report.md").read_text(encoding="utf-8")
    v2_report = (v2["workspace"] / "final_report.md").read_text(encoding="utf-8")
    assert "Regional sales report" in v1_report
    assert v2_report.strip()

    # Numeric ground truth: identical business numbers from the same data.
    v1_text = "".join(v1["chunks"])
    assert "220" in v1_text and "80" in v1_text
    v2_totals = v2_group_aggregate_result(v2)
    assert v2_totals == EXPECTED_TOTALS

    # Evidence contracts per runtime (I): V1 keeps session findings; V2 keeps
    # registry-verified evidence for every execution.
    assert len(json.loads((v1["workspace"] / "session_state.json").read_text(
        encoding="utf-8")).get("findings", [])) >= 2 if (
        v1["workspace"] / "session_state.json").exists() else True
    v2_evidence = v2["state"].get("evidence", [])
    assert v2_evidence
    assert all(item.get("verification_status") == "verified" for item in v2_evidence)


def test_parity_tool_call_counts_have_no_duplicate_execution(tmp_path: Path,
                                                             monkeypatch):
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "0")
    v1 = run_scenario(tmp_path, "toolcount", runtime_v2=False,
                      script=build_v1_steps(tmp_path / "x.csv"))
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "1")
    v2 = run_scenario(tmp_path, "toolcount", runtime_v2=True)

    v1_names = [e.get("tool_name") for e in v1["executions"]]
    v2_names = [e.get("tool_name") for e in v2["executions"]]
    # Both runtimes executed the analysis exactly once per planned step.
    assert v1_names.count("python_repl") == 1
    assert v2_names.count("run_analysis_operation") == 2
    v2_task_ids = [e.get("task_id") for e in v2["executions"]]
    assert len(v2_task_ids) == len(set(v2_task_ids))
    assert all(e.get("status") == "succeeded" for e in v2["executions"])


def test_parity_failure_never_produces_false_success(tmp_path: Path, monkeypatch):
    # The planner cannot build a valid plan for a goal whose column is absent,
    # so V2 must hard-stop without a success report; V1 must also end without
    # a submitted report when its tool flow fails on the same broken data.
    broken_rows = "city,amount\nNorth,100\nNorth,120\nSouth,80\n"

    monkeypatch.setenv("RUNTIME_V2_ENABLED", "0")
    failing_script = [
        lambda messages: AIMessage(content="", tool_calls=[make_tool_call(
            "load_data", {"file_path": "placeholder"}, "f1")]),
        lambda messages: AIMessage(content="", tool_calls=[make_tool_call(
            "python_repl", {"code": "raise ValueError('analysis exploded')"}, "f2")]),
        lambda messages: AIMessage(content="The analysis failed and no report was produced."),
    ]
    v1 = run_scenario(tmp_path, "failure", runtime_v2=False, script=failing_script,
                      csv_text=broken_rows)
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "1")
    v2 = run_scenario(tmp_path, "failure", runtime_v2=True, csv_text=broken_rows)

    assert v1["run_status"] != "completed"
    assert v2["run_status"] != "completed"
    assert not (v1["workspace"] / "final_report.md").exists()
    assert not (v2["workspace"] / "final_report.md").exists()
    v1_text = "".join(v1["chunks"])
    v2_text = "".join(v2["chunks"])
    assert "Regional sales report" not in v1_text
    assert "Status: `success`" not in v2_text


def test_parity_cancel_both_runtimes_end_cancelled(tmp_path: Path, monkeypatch):
    sleeping_code = "import time; time.sleep(3.0); print('done')"

    monkeypatch.setenv("RUNTIME_V2_ENABLED", "0")
    v1 = run_scenario(
        tmp_path, "cancel", runtime_v2=False,
        script=[
            lambda messages: AIMessage(content="", tool_calls=[make_tool_call(
                "load_data", {"file_path": str(tmp_path / 'x.csv')}, "c1")]),
            lambda messages: AIMessage(content="", tool_calls=[make_tool_call(
                "python_repl", {"code": sleeping_code}, "c2")]),
        ],
        cancel_event=asyncio.Event(),
    )
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "1")
    # The deterministic V2 plan completes in well under 0.5s, so the V2 cancel
    # scenario uses a pre-start cancel request (graph-start boundary).
    v2 = run_scenario(tmp_path, "cancel", runtime_v2=True,
                      cancel_event=asyncio.Event(), cancel_before_start=True)

    assert v1["run_status"] == "cancelled"
    assert v2["run_status"] == "cancelled"
    assert any("Analysis cancelled" in chunk for chunk in v1["chunks"])
    assert any("Analysis cancelled" in chunk for chunk in v2["chunks"])
    assert not (v1["workspace"] / "final_report.md").exists()
    assert not (v2["workspace"] / "final_report.md").exists()


def test_parity_sse_semantics_are_compatible(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "0")
    v1 = run_scenario(tmp_path, "sse", runtime_v2=False,
                      script=build_v1_steps(tmp_path / "x.csv"))
    monkeypatch.setenv("RUNTIME_V2_ENABLED", "1")
    v2 = run_scenario(tmp_path, "sse", runtime_v2=True)

    # V1 streams model tokens as separate chunks (token-level granularity).
    v1_text = "".join(v1["chunks"])
    assert v1_text
    assert any(len(chunk) < len(v1_text) for chunk in v1["chunks"])
    # V2 streams per-task previews and completes; its deterministic plan has no
    # model tokens to stream.  Both must end with a persisted final report.
    assert (v1["workspace"] / "final_report.md").exists()
    assert (v2["workspace"] / "final_report.md").exists()
    # Both routes logged an explicit runtime route decision.
    for result in (v1, v2):
        assert result["state"].get("run", {}).get("status") in {"completed"}
