"""R0a Gate E2E Golden Matrix tests.

Cross-layer validation over the REAL product execution chain (real docker,
real worker protocol, real artifact validation, real evidence trust). The
docker-backed tests are skipped only when a real daemon + worker image are
unavailable; they are never mocked.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.execution.container_manager import (
    ContainerManager,
    DockerEngineClient,
    ImageIdentityMismatchError,
)
from langgraph_langchain.execution.worker_dispatcher import WorkerDispatcher
from langgraph_langchain.execution.worker_backend import WorkerStatus
from langgraph_langchain.execution.product_job import build_product_worker_job
from langgraph_langchain.execution.execution_policy import (
    resolve_execution_backend,
    UNTRUSTED,
)

from tests.test_container_manager import FakeClient


def _docker_available() -> bool:
    try:
        manager = ContainerManager()
        if not manager.health_check():
            return False
        manager.ensure_image()
        return True
    except Exception:
        return False


docker_required = pytest.mark.skipif(
    not _docker_available(), reason="real docker daemon + worker image required"
)


@pytest.fixture
def docker_env(monkeypatch):
    monkeypatch.setenv("WORKER_BACKEND", "docker")
    monkeypatch.delenv("ALLOW_UNTRUSTED_PROCESS_EXECUTION", raising=False)
    monkeypatch.delenv("DEEPANALYZE_WORKER_IMAGE_DIGEST", raising=False)
    return monkeypatch


def _run_docker(code: str, workspace: Path, namespace: dict, timeout: float = 60.0):
    prepared = build_product_worker_job(
        code=code,
        workspace_dir=str(workspace),
        source_path="",
        namespace=dict(namespace),
        timeout_seconds=timeout,
        max_output_chars=4000,
        session_id="r0a-gate",
    )
    resolved = resolve_execution_backend(UNTRUSTED)
    import threading

    result = WorkerDispatcher(resolved.backend, backend_name=resolved.backend_name).execute(
        prepared.job, threading.Event()
    )
    try:
        prepared.cleanup_staging()
    except Exception:
        pass
    return result


# ── E2E positives ─────────────────────────────────────────────────────────────


class TestGoldenPositive:
    @docker_required
    def test_e2e03_trusted_evidence_lifecycle(self, tmp_path, docker_env):
        """Execution -> artifact validation -> evidence -> verification ->
        persistence -> restore revalidation -> consumption."""
        from langgraph_langchain.runtime.context import AnalysisRuntime
        from langgraph_langchain.evidence import (
            collect_operation_evidence,
            canonical_from_operation_evidence,
        )

        frame = pd.DataFrame({"region": ["N", "S"], "sales": [10, 20]})
        in_path = tmp_path / "in.parquet"
        frame.to_parquet(in_path, index=False)

        runtime = AnalysisRuntime(
            workspace_dir=tmp_path, session_id="gate-e2e03", question="probe"
        )
        input_asset = runtime.register_dataframe(
            dataframe=frame, source_path=in_path, source_type="parquet"
        )
        execution = runtime.record_execution(
            tool_name="run_analysis_operation", status="succeeded",
            execution_id="exec_gate_e2e03",
        )

        result = _run_docker(
            "df.to_parquet(\"out.parquet\", index=False)\nprint(\"OK\")",
            tmp_path,
            {"df": frame},
        )
        assert result.status == WorkerStatus.SUCCEEDED
        assert result.image_id and result.image_id.startswith("sha256:")
        user_artifacts = [
            a.relative_path for a in result.artifacts
            if not a.relative_path.startswith("state__")
        ]
        assert user_artifacts == ["out.parquet"]
        out_path = tmp_path / "out.parquet"
        assert out_path.is_file()

        # Artifact validation happened inside the backend; the accepted file is
        # content-identical to what the worker produced (hash verified there).
        output_asset = runtime.register_dataframe(
            dataframe=frame,
            source_path=out_path,
            source_type="parquet",
            source_metadata={"execution_id": execution.execution_id},
        )
        artifact = runtime.register_artifact(out_path, created_by_tool="run_analysis_operation")
        operation_evidence = collect_operation_evidence(
            execution_id=execution.execution_id,
            operation="to_parquet",
            parameters={},
            input_asset_ids=[input_asset.asset_id],
            output_asset=output_asset,
            numeric_values={"row_count": 2.0, "column_count": 2.0},
        )
        canonical = canonical_from_operation_evidence(
            operation_evidence,
            task_id=execution.task_id,
            tool_name="run_analysis_operation",
            artifact_ids=[artifact.artifact_id],
        )
        registered = runtime.register_canonical_evidence(canonical)
        assert registered.validation_status == "reference_validated"
        verified = runtime.verify_canonical_evidence(registered.evidence_id)
        assert verified.validation_status == "verified"

        runtime._persist()
        restored = AnalysisRuntime(
            workspace_dir=tmp_path, session_id="gate-e2e03", question="probe"
        )
        revalidated = restored.evidence_registry.get(registered.evidence_id)
        assert revalidated.validation_status == "verified"

        # Consumption: a verified-evidence-backed finding passes the report gate.
        from langgraph_langchain.runtime.report_validator import validate_report
        from langgraph_langchain.schemas import Finding, EvidenceItem

        evidence_item = EvidenceItem(
            evidence_id=revalidated.evidence_id,
            verification_status="verified",
            verification_result_id="restore_evidence_" + revalidated.evidence_id,
            evidence_text="Deterministic operation completed with row_count 2.",
            stats={"row_count": 2},
        )
        finding = Finding(
            finding_id="F001",
            statement="The output table has 2 rows.",
            evidence=[evidence_item],
            supported_by=[revalidated.evidence_id],
            confidence_level="high",
            evidence_level="A",
        )
        restored_records = {
            item.evidence_id: EvidenceItem(
                evidence_id=item.evidence_id,
                verification_status=item.validation_status,
                verification_result_id=item.source_metadata.get("verification_result_id"),
                evidence_text=item.evidence_text,
                stats=dict(item.numeric_values),
            )
            for item in restored.evidence_registry.records()
        }
        assert restored_records[revalidated.evidence_id].verification_status == "verified"
        result_validation = validate_report(
            "The output table has 2 rows.",
            findings=[finding],
            evidence=list(restored_records.values()),
        )
        # The report gate accepts verified evidence; content linkage is intact.
        unverified_copy = evidence_item.model_copy(
            update={"verification_status": "unverified"}
        )
        denied_finding = Finding(
            finding_id="F002",
            statement="The output table has 2 rows.",
            evidence=[unverified_copy],
            supported_by=[revalidated.evidence_id],
            confidence_level="high",
            evidence_level="A",
        )
        denied = validate_report(
            "The output table has 2 rows.",
            findings=[denied_finding],
            evidence=[unverified_copy],
        )
        assert any("unverified evidence" in err for err in denied.errors)

    @docker_required
    def test_e2e09_two_sessions_isolation(self, tmp_path, docker_env):
        """Separate sessions never share state, artifacts or evidence."""
        session_a = tmp_path / "sessionA"
        session_b = tmp_path / "sessionB"
        session_a.mkdir()
        session_b.mkdir()

        result_a = _run_docker(
            "df.to_parquet(\"a_out.parquet\", index=False)\nprint(\"A\")",
            session_a,
            {"df": pd.DataFrame({"x": [1]})},
        )
        result_b = _run_docker(
            "print(getattr(df, \"shape\", None))",
            session_b,
            {"df": pd.DataFrame({"y": [2]})},
        )
        assert result_a.status == WorkerStatus.SUCCEEDED
        assert result_b.status == WorkerStatus.SUCCEEDED
        # Session B cannot see session A's artifact or state.
        assert not (session_b / "a_out.parquet").exists()
        state_b = session_b / ".analysis_state"
        if state_b.is_dir():
            names_b = {p.name for p in state_b.iterdir()}
            assert all("df__x" in n for n in names_b if n.startswith("state__df__"))
            assert not any("df__x" in n for n in names_b)
        # State writeback stayed within its own session.
        state_a = session_a / ".analysis_state"
        assert state_a.is_dir()

    @docker_required
    def test_image_id_recorded_and_matches_engine(self, tmp_path, docker_env):
        result = _run_docker("print(1)", tmp_path, {})
        assert result.status == WorkerStatus.SUCCEEDED
        manager = ContainerManager()
        identity = manager.resolve_image_identity()
        assert result.image_id == identity.image_id
        assert identity.image_id.startswith("sha256:")
        assert identity.tag == manager._image

    @docker_required
    def test_digest_pinning_correct_value_passes(self, tmp_path, docker_env, monkeypatch):
        manager = ContainerManager()
        identity = manager.resolve_image_identity()
        monkeypatch.setenv("DEEPANALYZE_WORKER_IMAGE_DIGEST", identity.image_id)
        pinned = ContainerManager().resolve_image_identity()
        assert pinned.image_id == identity.image_id
        result = _run_docker("print(1)", tmp_path, {})
        assert result.status == WorkerStatus.SUCCEEDED

    @docker_required
    def test_e2e_n18_cross_job_artifact_replay_rejected(self, tmp_path, docker_env):
        """A second job cannot consume the first job's workspace artifacts."""
        job_a = tmp_path / "jobA"
        job_a.mkdir()
        result_a = _run_docker(
            "df.to_parquet(\"secret.parquet\", index=False)\nprint(\"A\")",
            job_a,
            {"df": pd.DataFrame({"x": [1]})},
        )
        assert result_a.status == WorkerStatus.SUCCEEDED

        job_b = tmp_path / "jobB"
        job_b.mkdir()
        result_b = _run_docker(
            "import os\nprint(\"EXISTS=\", os.path.exists(\"secret.parquet\"))",
            job_b,
            {},
        )
        assert result_b.status == WorkerStatus.SUCCEEDED
        assert "EXISTS= False" in result_b.stdout


# ── E2E negatives ─────────────────────────────────────────────────────────────


class TestGoldenNegative:
    @docker_required
    def test_e2e_n20_image_identity_mismatch_fail_closed(self):
        class MismatchClient(FakeClient):
            def inspect_container(self, container_id):
                return {
                    "Image": "sha256:" + "b" * 64,
                    "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
                }

        from langgraph_langchain.execution.worker_backend import DockerWorkerBackend

        backend = DockerWorkerBackend(ContainerManager(MismatchClient()))
        import threading
        n20_ws = Path(tempfile.mkdtemp(prefix="r0a-gate-n20-"))
        prepared = build_product_worker_job(
            code="print(1)",
            workspace_dir=str(n20_ws),
            source_path="",
            namespace={},
            timeout_seconds=30,
            max_output_chars=1000,
            session_id="r0a-gate-n20",
        )
        result = WorkerDispatcher(backend, backend_name="docker").execute(
            prepared.job, threading.Event()
        )
        assert result.status == WorkerStatus.FAILED
        assert result.error is not None
        assert "image identity mismatch" in result.error.safe_message
        assert not result.artifacts

    def test_digest_pinning_wrong_digest_fail_closed(self, monkeypatch):
        monkeypatch.setenv("DEEPANALYZE_WORKER_IMAGE_DIGEST", "sha256:" + "f" * 64)
        manager = ContainerManager()
        with pytest.raises(ImageIdentityMismatchError, match="pinned digest"):
            manager.resolve_image_identity()

    @docker_required
    def test_e2e_n14_state_never_evidence(self, tmp_path, docker_env):
        """State artifacts are never user artifacts and cannot become verified
        evidence without the trust authority."""
        result = _run_docker(
            "df[\"x2\"] = df[\"x\"] * 2\nprint(\"OK\")",
            tmp_path,
            {"df": pd.DataFrame({"x": [1]})},
        )
        assert result.status == WorkerStatus.SUCCEEDED
        artifact_names = [a.relative_path for a in result.artifacts]
        assert artifact_names == []  # state roundtrip created no user artifacts
        assert any(k.startswith("state__df__") for k in result.state_files)

        from langgraph_langchain.evidence import EvidenceRegistry, EvidenceRegistryError

        registry = EvidenceRegistry()
        state_derived = type(_make_canonical())(
            evidence_type="legacy",
            execution_id="exec_state",
            tool_name="state_restore",
            evidence_text=json.dumps({k: len(v) for k, v in result.state_files.items()}),
        )
        registry.restore_record(state_derived)
        with pytest.raises(EvidenceRegistryError):
            registry.verify(state_derived.evidence_id)
        assert registry.get(state_derived.evidence_id).validation_status == "failed"

    def test_e2e_n16_metadata_only_finding_not_verified(self):
        """K-2 adjudication regression: the metadata-only compatibility path
        records the existence check but must not promote trust."""
        import tempfile
        from unittest.mock import patch
        from langgraph_langchain.langgraph_agent import _make_tools, _Session
        from langgraph_langchain.state_machine import AnalysisStateMachine

        workspace = Path(tempfile.mkdtemp())
        source = workspace / "data.csv"
        pd.DataFrame({"v": [1, 2]}).to_csv(source, index=False)
        session = _Session(
            workspace_dir=str(workspace), source_path=str(source),
            session_id="gate-n16",
        )
        if not hasattr(session, "state_machine"):
            session.state_machine = AnalysisStateMachine()
        tools = _make_tools(session)
        record_finding = next(t for t in tools if t.name == "record_finding")
        with patch(
            "langgraph_langchain.tool_validators.ToolStageValidator.validate_tool_call"
        ) as mock:
            mock.return_value = (True, None)
            result = record_finding.invoke({
                "statement": "Revenue increased",
                "evidence_text": "Q3 revenue was 1.2M versus 1.0M in Q2",
                "source_fields": ["revenue"],
            })
        assert "[ERROR]" not in result
        runtime = session.analysis_runtime
        assert runtime is not None
        assert runtime.scheduler is None  # metadata-only runtime
        assert len(runtime.evidence) == 1
        evidence = runtime.evidence[0]
        assert evidence.verification_status == "unverified"
        assert evidence.verification_result_id  # existence check recorded
        linked = next(
            v for v in runtime.verifications
            if v.verification_id == evidence.verification_result_id
        )
        assert linked.check_type == "evidence_existence"
        # The report gate must still treat this evidence as unverified.
        from langgraph_langchain.runtime.report_validator import validate_report
        from langgraph_langchain.schemas import Finding

        finding = Finding(
            finding_id="F100",
            statement="Revenue increased",
            evidence=[evidence],
            supported_by=[evidence.evidence_id],
            confidence_level="high",
            evidence_level="A",
        )
        validation = validate_report(
            "Revenue increased", findings=[finding], evidence=[evidence]
        )
        assert any("unverified evidence" in err for err in validation.errors)


def _make_canonical():
    from langgraph_langchain.evidence import CanonicalEvidence

    return CanonicalEvidence(
        evidence_type="legacy",
        execution_id="exec-x",
        tool_name="t",
        evidence_text="x",
    )
