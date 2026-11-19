"""A1.3.2.2 - Linux Docker worker cancellation & process-tree validation.

Runs only on Linux (uses /proc, os.killpg, process groups). Complements the
A1.2 cancellation suite with process-tree-level evidence.

Threading model matches production: the graph node runs sync executors via
asyncio's default executor thread (langgraph pregel _call.py run_in_executor),
so every scenario here drives execute() through asyncio.run + run_in_executor
and delivers cancellation via loop.call_later.

Scenarios:
- C2/C3: cancel during execution terminates the worker AND its spawned child
  processes (no orphans) - verified by scanning /proc cmdline.
- C4: a worker that ignores SIGTERM is escalated to SIGKILL.
- C6: repeated cancellation is idempotent.
- C7: execution timeout terminates worker + spawned child (timed_out).
- C3b: worker calls setsid -> PGID == PID (killpg(process.pid) basis).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from langgraph_langchain.execution.python_executor import IsolatedPythonExecutor
from langgraph_langchain.execution.models import PythonExecutionRequest

pytestmark = pytest.mark.skipif(
    not hasattr(os, "killpg") or not Path("/proc").is_dir(),
    reason="Linux-only validation (requires /proc and killpg)",
)


def _make_request(code: str, workspace_dir: str, timeout_seconds: float) -> PythonExecutionRequest:
    return PythonExecutionRequest(
        code=code, workspace_dir=workspace_dir, timeout_seconds=timeout_seconds
    )


def _proc_pids(match: str) -> list[int]:
    hits: list[int] = []
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


async def _async_wait(condition, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return condition()


def test_c2_c3_cancel_terminates_worker_and_child_no_orphans(tmp_path: Path):
    """Worker spawns a child `sleep 60`; cancel must kill both (C2 + C3)."""
    executor = IsolatedPythonExecutor()
    cancel_event = asyncio.Event()
    code = (
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        "print('CHILD_PID', child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    request = _make_request(code, str(tmp_path), 60.0)

    async def run():
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        # Production threading model: sync executor in the asyncio default
        # executor thread (same as langgraph pregel run_in_executor).
        task = loop.run_in_executor(
            None, lambda: executor.execute(request, cancel_event=cancel_event)
        )
        # Wait until the child process actually exists.
        assert await _async_wait(lambda: bool(_proc_pids("sleep 60")), timeout=15), (
            "child never started"
        )
        assert _proc_pids("sleep 60"), "no sleep child found"

        await asyncio.sleep(0.2)
        cancel_event.set()
        result = await asyncio.wait_for(task, timeout=30)
        elapsed = time.monotonic() - started
        return result, elapsed

    result, elapsed = asyncio.run(run())

    assert result.status == "cancelled", f"status={result.status}"
    assert elapsed < 10, f"cancel took {elapsed:.2f}s"
    # No orphans: the spawned sleep child must be gone.
    assert _proc_pids("sleep 60") == [] or not _wait_for_sync(
        lambda: bool(_proc_pids("sleep 60")), timeout=10
    ), f"orphaned sleep processes remain: {_proc_pids('sleep 60')}"


def _wait_for_sync(condition, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


def test_c4_sigterm_ignored_worker_escalates_to_sigkill(tmp_path: Path):
    """Worker ignores SIGTERM; executor must escalate to SIGKILL (C4)."""
    executor = IsolatedPythonExecutor()
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('SIGTERM_IGN_READY', flush=True)\n"
        "time.sleep(180)\n"
    )
    request = _make_request(code, str(tmp_path), 90.0)

    async def run():
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        cancel_event = asyncio.Event()
        task = loop.run_in_executor(
            None, lambda: executor.execute(request, cancel_event=cancel_event)
        )
        await asyncio.sleep(1.5)
        cancel_event.set()
        result = await asyncio.wait_for(task, timeout=30)
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(run())

    assert result.status == "cancelled"
    # SIGTERM grace ~5s; SIGKILL escalation must complete well before the 90s
    # execution timeout.
    assert elapsed < 15, f"escalation took {elapsed:.2f}s (SIGKILL path broken?)"
    assert elapsed >= 0.5, "worker died before SIGTERM grace could be relevant"


def test_c6_repeated_cancel_is_idempotent(tmp_path: Path):
    """Setting the cancel event repeatedly must not corrupt state (C6)."""
    executor = IsolatedPythonExecutor()
    code = "import time; time.sleep(120)\n"
    request = _make_request(code, str(tmp_path), 60.0)

    async def run():
        loop = asyncio.get_running_loop()
        cancel_event = asyncio.Event()
        task = loop.run_in_executor(
            None, lambda: executor.execute(request, cancel_event=cancel_event)
        )
        await asyncio.sleep(1.0)
        cancel_event.set()
        cancel_event.set()
        cancel_event.set()
        return await asyncio.wait_for(task, timeout=30)

    result = asyncio.run(run())
    assert result.status == "cancelled"


def test_c7_timeout_kills_worker_and_children(tmp_path: Path):
    """Execution timeout terminates worker + spawned child (C7)."""
    executor = IsolatedPythonExecutor()
    code = (
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        "print('CHILD_PID', child.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    request = _make_request(code, str(tmp_path), 2.0)
    started = time.monotonic()
    result = executor.execute(request)
    elapsed = time.monotonic() - started

    assert result.status == "timed_out"
    assert result.error_type == "TimeoutError"
    assert elapsed < 15, f"timeout handling took {elapsed:.2f}s"
    assert not _wait_for_sync(
        lambda: bool(_proc_pids("sleep 60")), timeout=10
    ), f"orphaned sleep children after timeout: {_proc_pids('sleep 60')}"


def test_c3_worker_group_is_session_leader(tmp_path: Path):
    """Worker calls setsid: its PGID must equal its own PID (killpg basis)."""
    executor = IsolatedPythonExecutor()
    code = (
        "import os, time\n"
        "print('PGID', os.getpgid(0), 'PID', os.getpid(), flush=True)\n"
        "time.sleep(0.3)\n"
    )
    request = _make_request(code, str(tmp_path), 30.0)
    result = executor.execute(request)
    assert result.status == "succeeded"
    assert "PGID" in result.stdout
    fields = dict(zip(result.stdout.split()[0::2], result.stdout.split()[1::2]))
    assert fields["PID"] == fields["PGID"], (
        "worker is not a process-group leader; killpg(process.pid) would miss the tree"
    )
