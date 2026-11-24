"""R0a-1B real Docker product-path E2E tests.

These tests exercise the REAL product execution chain with a real Docker
daemon:

    Agent tool / _Session.run_code / PythonTaskExecutor
        -> ExecutionPolicy (WORKER_BACKEND=docker)
        -> WorkerDispatcher
        -> DockerWorkerBackend
        -> real container (deepanalyze-worker image)
        -> worker protocol
        -> artifact validation + acceptance
        -> state restore / runtime result

No Docker mock is used for the Docker part. Tests are skipped only when a
usable Docker daemon or the worker image is unavailable on the test host.
"""

import json
import os
import socket
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from langgraph_langchain.execution.container_manager import ContainerManager
from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor


def _docker_available() -> bool:
    try:
        manager = ContainerManager()
        if not manager.health_check():
            return False
        manager.ensure_image()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="real docker daemon + deepanalyze-worker image required",
)


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {"region": ["North", "South", "East", "West"], "sales": [100, 200, 150, 300]}
    )


@pytest.fixture
def docker_env(monkeypatch):
    monkeypatch.setenv("WORKER_BACKEND", "docker")
    monkeypatch.delenv("ALLOW_UNTRUSTED_PROCESS_EXECUTION", raising=False)
    return monkeypatch


# ── E2E-01: basic python execution through the product run_code path ─────────

class TestRunCodeDockerE2E:
    def test_basic_execution_runs_in_container_not_host(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-01", user_question="probe")
        session.ns["df"] = sample_df
        host_pid = os.getpid()
        output = session.run_code(
            "import os\nprint('SUM=', 2 + 3)\nprint('PID=', os.getpid())\nprint('ROWS=', len(df))"
        )
        assert "[ERROR]" not in output
        assert "[SECURITY_DENIED]" not in output
        assert "SUM= 5" in output
        assert "ROWS= 4" in output
        # execution happened inside the container: host pid never appears and
        # the worker pid differs from the host process
        assert f"PID= {host_pid}" not in output
        assert "PID= " in output
        worker_pid = int(output.split("PID= ")[1].splitlines()[0])
        assert worker_pid != host_pid

    def test_namespace_state_roundtrip_across_calls(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-state", user_question="probe")
        session.ns["df"] = sample_df
        output = session.run_code(
            "df['sales2'] = df['sales'] * 2\ndf2 = df.head(2)\nprint('COLS=', sorted(df.columns))"
        )
        assert "[ERROR]" not in output
        assert "COLS=" in output and "'sales2'" in output
        # state restored into the host namespace
        assert "sales2" in session.ns["df"].columns
        assert list(session.ns["df"]["sales2"]) == [200, 400, 300, 600]
        assert len(session.ns["df2"]) == 2
        # second call observes the restored state
        output2 = session.run_code("print('DF2ROWS=', len(df2))")
        assert "[ERROR]" not in output2
        assert "DF2ROWS= 2" in output2

    def test_artifact_generation_is_accepted_into_workspace(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-artifact", user_question="probe")
        session.ns["df"] = sample_df
        output = session.run_code(
            "import json\n"
            "with open('result.json', 'w') as f:\n"
            "    json.dump({'status': 'ok', 'value': 42}, f)\n"
            "print('WROTE')"
        )
        assert "[ERROR]" not in output
        artifact = tmp_path / "result.json"
        assert artifact.is_file()
        assert json.loads(artifact.read_text()) == {"status": "ok", "value": 42}

    def test_timeout_is_enforced_by_the_container(self, tmp_path, sample_df, docker_env, monkeypatch):
        from langgraph_langchain.langgraph_agent import _Session
        import langgraph_langchain.langgraph_agent as agent_module

        monkeypatch.setattr(agent_module, "_CODE_TIMEOUT", 8)
        session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-timeout", user_question="probe")
        session.ns["df"] = sample_df
        output = session.run_code("import time\ntime.sleep(120)\nprint('NEVER')")
        assert "NEVER" not in output
        assert ("[ERROR]" in output) or ("timed out" in output.lower())

    def test_source_file_is_staged_and_readable(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        source = tmp_path / "input_data.csv"
        sample_df.to_csv(source, index=False)
        session = _Session(str(tmp_path), str(source), session_id="r0a1b-e2e-source", user_question="probe")
        output = session.run_code(
            "import pandas as pd\n"
            "direct = pd.read_csv(SOURCE_PATH)\n"
            "print('DIRECT_ROWS=', len(direct))\n"
            "via_helper = load_csv('input_data.csv')\n"
            "print('HELPER_ROWS=', len(via_helper))"
        )
        assert "[ERROR]" not in output
        assert "DIRECT_ROWS= 4" in output
        assert "HELPER_ROWS= 4" in output


# ── E2E-06 + security negative tests (runtime container evidence) ────────────

class TestDockerIsolationE2E:
    def test_container_runs_non_root_with_isolated_host_view(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        # host sentinel: must NOT be visible inside the container
        sentinel = Path("/tmp/r0a1b_host_sentinel_unique")
        sentinel.write_text("host-secret")
        try:
            session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-sec", user_question="probe")
            session.ns["df"] = sample_df
            output = session.run_code(
                "import os, socket\n"
                "print('UID=', os.getuid())\n"
                "print('HOST_SENTINEL=', os.path.exists('/tmp/r0a1b_host_sentinel_unique'))\n"
                "try:\n"
                "    with open('/usr/r0a1b_probe_file', 'w') as f:\n"
                "        f.write('x')\n"
                "    print('ROOTFS_WRITE=allowed')\n"
                "except OSError:\n"
                "    print('ROOTFS_WRITE=denied')\n"
                "s = socket.socket()\n"
                "s.settimeout(3)\n"
                "try:\n"
                "    s.connect(('8.8.8.8', 53))\n"
                "    print('NETWORK=reachable')\n"
                "except OSError:\n"
                "    print('NETWORK=blocked')\n"
                "finally:\n"
                "    s.close()\n"
            )
            assert "[ERROR]" not in output
            assert "UID= 10001" in output
            assert "HOST_SENTINEL= False" in output
            assert "ROOTFS_WRITE=denied" in output
            assert "NETWORK=blocked" in output
        finally:
            sentinel.unlink(missing_ok=True)

    def test_container_runtime_config_is_enforced(self, tmp_path):
        """Inspect a real managed container while it runs."""
        manager = ContainerManager()
        manager.ensure_image()
        job_id = "job_inspectcheck0000000000000000000"
        envelope = {
            "protocol_version": "1",
            "kind": "job",
            "job": {
                "job_id": job_id,
                "code": "import time\ntime.sleep(30)",
            },
        }
        workspace = manager.create_workspace(
            job_id, json.dumps(envelope).encode("utf-8")
        )
        from langgraph_langchain.execution.container_manager import ContainerSpec

        spec = ContainerSpec(
            job_id=job_id,
            input_dir=str(workspace.root / "input"),
            output_dir=str(workspace.root / "output"),
            tmp_dir=str(workspace.root / "tmp"),
        )
        handle = manager.run_container(spec)
        try:
            info = manager._client.inspect_container(handle.container_id)
            config = info["Config"]
            host_config = info["HostConfig"]
            assert config["User"] == "10001:10001"
            assert host_config["Privileged"] is False
            assert host_config["NetworkMode"] == "none"
            assert host_config["ReadonlyRootfs"] is True
            assert "ALL" in (host_config.get("CapDrop") or [])
            assert "no-new-privileges" in (host_config.get("SecurityOpt") or [])
            assert host_config["PidsLimit"] == 128
            assert host_config["Memory"] == 512 * 1024 * 1024
            assert host_config["NanoCpus"] == 1000000000
            assert config["Image"].startswith("deepanalyze-worker:")
            assert info["State"]["Running"] is True
        finally:
            manager.terminate(handle)
            manager.cleanup(handle)

    def test_two_jobs_cannot_access_each_others_workspace(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.langgraph_agent import _Session

        workspace_a = tmp_path / "sessionA"
        workspace_b = tmp_path / "sessionB"
        session_a = _Session(str(workspace_a), "", session_id="r0a1b-iso-a", user_question="probe")
        session_b = _Session(str(workspace_b), "", session_id="r0a1b-iso-b", user_question="probe")
        output_a = session_a.run_code(
            "with open('secret_a.txt', 'w') as f:\n    f.write('task-a')\nprint('A_DONE')"
        )
        assert "[ERROR]" not in output_a
        assert (workspace_a / "secret_a.txt").read_text() == "task-a"
        # Task B cannot see Task A's workspace (different container mounts, and
        # the host session workspaces are not mounted into worker containers).
        output_b = session_b.run_code(
            "import os\nprint('B_SEES_A=', os.path.exists('/workspace/output/secret_a.txt'))\nprint('B_SEES_HOST=', os.path.exists('%s'))" % str(workspace_a / "secret_a.txt")
        )
        assert "[ERROR]" not in output_b
        assert "B_SEES_A= False" in output_b
        assert "B_SEES_HOST= False" in output_b


# ── Docker backend failure: fail closed, no process fallback ─────────────────

class TestDockerFailureFailClosed:
    def test_missing_image_fails_closed_without_process_fallback(self, tmp_path, sample_df, monkeypatch):
        from langgraph_langchain.langgraph_agent import _Session

        monkeypatch.setenv("WORKER_BACKEND", "docker")
        monkeypatch.delenv("ALLOW_UNTRUSTED_PROCESS_EXECUTION", raising=False)
        from langgraph_langchain.execution import container_manager as cm
        from langgraph_langchain.execution.worker_backend import ImageMissingError

        def _missing_image(self):
            raise ImageMissingError("deepanalyze-worker:does-not-exist")

        monkeypatch.setattr(cm.ContainerManager, "ensure_image", _missing_image)
        session = _Session(str(tmp_path), "", session_id="r0a1b-e2e-fail", user_question="probe")
        session.ns["df"] = sample_df
        output = session.run_code("print('SHOULD_NOT_RUN')")
        assert "SHOULD_NOT_RUN" not in output
        assert "[ERROR]" in output
        assert ("[SECURITY_DENIED]" not in output)


# ── E2E via delegate tool and Runtime V2 PythonTaskExecutor ──────────────────

class TestOtherProductEntriesDockerE2E:
    def test_delegate_analysis_runs_in_container(self, sample_df, tmp_path, docker_env):
        from unittest.mock import MagicMock

        from langgraph_langchain.state_machine import AnalysisStage, AnalysisStateMachine
        from langgraph_langchain.tools.registry import registry

        session = MagicMock()
        session.workspace_dir = tmp_path
        session.session_id = "r0a1b-delegate-docker"
        session.source_path = ""
        session.cancel_event = threading.Event()
        session.current_stage = "deep_dive"
        session.findings = []
        session.metric_definitions = []
        session.assumptions = []
        session.new_artifacts = []
        session.known_image_files = set()
        session.consecutive_python_errors = 0
        session._delegation_depth = 0
        session.ns = {"df": sample_df, "WORKSPACE_DIR": str(tmp_path)}
        sm = AnalysisStateMachine()
        sm.current_stage = AnalysisStage.DEEP_DIVE
        sm.tools_used = ["load_data", "eda_profile"]
        sm.conditions_met = {"data_loaded", "schema_documented", "quality_assessed"}
        session.state_machine = sm
        session.logger = MagicMock()
        session.structured_logger = MagicMock()

        entry = registry._tools.get("delegate_analysis")
        tool_fn = entry.factory(session)
        result = json.loads(
            tool_fn.invoke(
                {
                    "task_description": "docker delegate",
                    "analysis_type": "general",
                    "code": "import os\nprint('PID=', os.getpid())\nprint('SUM=', df['sales'].sum())",
                }
            )
        )
        assert result["success"] is True
        assert f"PID= {os.getpid()}" not in result["output"]
        assert "SUM= 750" in result["output"]
        assert session._delegation_depth == 0

    def test_runtime_python_task_runs_in_container(self, tmp_path, sample_df, docker_env):
        from langgraph_langchain.execution.task_models import TaskExecutionRequest
        from langgraph_langchain.schemas import AnalysisTask

        (tmp_path / "in.csv").write_text("a,b\n1,2\n3,4\n")
        executor = PythonTaskExecutor(workspace_dir=str(tmp_path))
        task = AnalysisTask(
            session_id="r0a1b-task-docker",
            question="container task probe",
            executor_type="python",
        )
        request = TaskExecutionRequest(
            run_id="run-1",
            execution_id="exec-1",
            task=task,
            code="import pandas as pd\ndf = pd.read_csv('in.csv')\nprint('TASK_ROWS=', len(df))",
            source_path=str(tmp_path / "in.csv"),
            workspace_dir=str(tmp_path),
            timeout_seconds=90,
            max_output_chars=4000,
        )
        result = executor.execute(request)
        assert result.status == "succeeded", result.error
        assert "TASK_ROWS= 2" in result.stdout_preview
        # artifact accepted into the session workspace (if produced)
        # stdout proves real container execution through the product chain
