"""C2/C3 via the production graph path (mirrors A1.2 cancellation test)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from langgraph_langchain.execution.python_executor import IsolatedPythonExecutor
from langgraph_langchain.langgraph_agent import (
    _make_tools,
    _run_runtime_v2_graph_stream,
    _Session,
)
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


def write_sales_csv(workspace: Path) -> Path:
    source = workspace / "sales.csv"
    source.write_text(
        "date,department,revenue\n"
        "2026-01-05,East,100\n"
        "2026-01-12,West,80\n",
        encoding="utf-8",
    )
    return source


def _proc_pids(match: str) -> list[int]:
    hits = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace").replace(chr(0), " ")
        except OSError:
            continue
        if match in cmdline:
            hits.append(int(entry.name))
    return hits


def _build_plan(child_code: str) -> AnalysisPlan:
    first = RuntimePlanStep(
        objective="Spawn a child process and hold",
        method="python_repl",
        expected_outputs=["analysis_output"],
    )
    second = RuntimePlanStep(
        objective="Second step must never run",
        method="python_repl",
        expected_outputs=["analysis_output"],
        depends_on=[first.step_id],
    )
    plan = AnalysisPlan(
        goal="Process tree cancellation E2E",
        success_criteria=["child spawns"],
        metrics=[],
        dimensions=[],
        steps=[first, second],
    )
    plan.steps[0].parameters["code"] = child_code
    plan.steps[1].parameters["code"] = "print('step2 done')"
    return plan


def _make_session(workspace: Path, source: Path) -> _Session:
    session = _Session(
        workspace_dir=str(workspace),
        source_path=str(source),
        session_id="a132_c2c3_e2e",
        user_question="Process tree cancellation check",
    )
    runtime = session.analysis_runtime
    assert runtime is not None
    runtime.initialize_plan(
        _build_plan(
            "import pathlib\n"
            "pathlib.Path('.marker_code_entered').write_text('1')\n"
            "import subprocess, time\n"
            "child = subprocess.Popen(['sleep', '60'])\n"
            "pathlib.Path('.marker_child_spawned').write_text(str(child.pid))\n"
            "print('CHILD_PID', child.pid, flush=True)\n"
            "time.sleep(120)\n"
        ),
        max_running_tasks=1,
    )
    return session


def test_c2_c3_graph_cancel_terminates_tree(tmp_path: Path):
    workspace = tmp_path / "c2c3"
    workspace.mkdir(parents=True)
    source = write_sales_csv(workspace)
    session = _make_session(workspace, source)
    runtime = session.analysis_runtime

    async def run():
        started = time.monotonic()
        chunks = []
        async for chunk, artifacts in _run_runtime_v2_graph_stream(
            session,
            tools=_make_tools(session),
            source_path=str(source),
            react_agent=None,
        ):
            chunks.append(chunk)
            if "CHILD_PID" in chunk:
                pass
        return chunks, time.monotonic() - started

    async def run_with_cancel():
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        chunks = []

        async def consume():
            async for chunk, artifacts in _run_runtime_v2_graph_stream(
                session,
                tools=_make_tools(session),
                source_path=str(source),
                react_agent=None,
            ):
                chunks.append(chunk)

        consume_task = asyncio.ensure_future(consume())
        # Wait until the worker's sleep child exists, then cancel.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _proc_pids("sleep 60"):
                break
            await asyncio.sleep(0.2)
        child_seen = bool(_proc_pids("sleep 60"))
        cancel_at = time.monotonic()
        loop.call_soon(session.cancel_event.set)
        await consume_task
        elapsed = time.monotonic() - started
        return chunks, child_seen, elapsed, cancel_at

    chunks, child_seen, elapsed, cancel_at = asyncio.run(run_with_cancel())

    assert any("Analysis cancelled" in chunk for chunk in chunks)
    statuses = [task.status for task in runtime.scheduler.tasks]
    assert statuses == ["cancelled", "cancelled"]
    assert runtime.run.status == "cancelled"
    assert session.runtime_v2_cancelled is True
    assert session.report is None

    # The worker must have spawned its child before cancel; the process-group
    # kill must leave no orphans.
    assert child_seen, "sleep child was never observed before cancel"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _proc_pids("sleep 60"):
        time.sleep(0.1)
    assert not _proc_pids("sleep 60"), (
        f"orphaned sleep processes remain: {_proc_pids('sleep 60')}"
    )
