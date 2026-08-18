from pathlib import Path
import threading

import pandas as pd

from langgraph_langchain.execution import IsolatedPythonExecutor, PythonExecutionRequest


def _request(tmp_path: Path, code: str, **kwargs):
    return PythonExecutionRequest(
        code=code, workspace_dir=str(tmp_path), source_path=str(tmp_path / "input.csv"),
        timeout_seconds=kwargs.pop("timeout_seconds", 2), max_output_chars=kwargs.pop("max_output_chars", 1000),
        **kwargs,
    )


def test_executor_returns_stdout_and_persistent_serializable_values(tmp_path):
    result = IsolatedPythonExecutor().execute(_request(tmp_path, "x = 4\nprint(x)"))
    assert result.status == "succeeded"
    assert result.stdout.strip() == "4"
    assert result.namespace_updates["x"] == 4


def test_executor_transfers_large_namespace_without_queue_deadlock(tmp_path):
    frame = pd.DataFrame({"value": range(200_000)})
    result = IsolatedPythonExecutor().execute(_request(
        tmp_path, "total = int(df['value'].sum())\nprint(total)",
        namespace={"df": frame}, timeout_seconds=10,
    ))
    assert result.status == "succeeded"
    assert result.namespace_updates["total"] == 19_999_900_000
    assert len(result.namespace_updates["df"]) == 200_000


def test_executor_returns_python_errors_without_crashing_parent(tmp_path):
    result = IsolatedPythonExecutor().execute(_request(tmp_path, "raise ValueError('bad input')"))
    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert "bad input" in result.stderr


def test_executor_terminates_timeout(tmp_path):
    result = IsolatedPythonExecutor().execute(_request(tmp_path, "while True: pass", timeout_seconds=0.2))
    assert result.status == "timed_out"


def test_executor_confines_save_fig_to_workspace(tmp_path):
    result = IsolatedPythonExecutor().execute(_request(tmp_path, "save_fig('../escaped.png')"))
    assert result.status == "failed"
    assert result.error_type == "ValueError"


def test_executor_registers_generated_workspace_artifacts(tmp_path):
    result = IsolatedPythonExecutor().execute(_request(
        tmp_path,
        "import matplotlib.pyplot as plt\nplt.plot([1, 2], [2, 3])\nsave_fig('chart.png')",
        timeout_seconds=10,
    ))
    assert result.status == "succeeded"
    assert (tmp_path / "chart.png").is_file()
    assert any(item["relative_path"] == "chart.png" for item in result.artifacts)


def test_executor_blocks_reads_outside_workspace_and_env_files(tmp_path):
    outside = IsolatedPythonExecutor().execute(_request(tmp_path, "Path('/etc/passwd').read_text()"))
    assert outside.status == "failed"
    assert outside.error_type == "PermissionError"
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    env_file = IsolatedPythonExecutor().execute(_request(tmp_path, "Path('.env').read_text()"))
    assert env_file.status == "failed"
    assert env_file.error_type == "PermissionError"


def test_executor_can_cancel_active_worker(tmp_path):
    event = threading.Event()
    threading.Timer(0.2, event.set).start()
    result = IsolatedPythonExecutor().execute(
        _request(tmp_path, "while True: pass", timeout_seconds=5), cancel_event=event,
    )
    assert result.status == "cancelled"
