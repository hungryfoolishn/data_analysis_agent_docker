from __future__ import annotations

from pathlib import Path

import pytest

from langgraph_langchain.execution.job_contract import WorkerJob
from langgraph_langchain.execution.product_job import build_product_worker_job
from langgraph_langchain.runtime import AnalysisRuntime
from langgraph_langchain.runtime.identity import RuntimeIdentityContext


def test_identity_context_rejects_untrusted_or_pathlike_values():
    with pytest.raises(ValueError):
        RuntimeIdentityContext.create(session_id="../escape")
    with pytest.raises(ValueError):
        RuntimeIdentityContext.create(session_id="ok", owner_id="bad owner")
    context = RuntimeIdentityContext.create(session_id="ok-session", owner_id="owner-a")
    assert context.owned is True
    assert context.worker_metadata() == {
        "schema_version": "runtime_identity.v1",
        "session_id": "ok-session",
        "owner_id": "owner-a",
    }


def test_runtime_task_persists_owner_id(tmp_path: Path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="r1_owner_session",
        owner_id="owner-a",
        question="owner propagation",
    )
    assert runtime.owner_id == "owner-a"
    assert runtime.task.owner_id == "owner-a"
    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id="r1_owner_session",
        owner_id="owner-a",
        question="owner propagation",
    )
    assert restored.task.owner_id == "owner-a"


def test_runtime_restore_rejects_owner_mismatch(tmp_path: Path):
    workspace = tmp_path / "workspace"
    AnalysisRuntime(
        workspace_dir=workspace,
        session_id="r1_mismatch_session",
        owner_id="owner-a",
        question="identity restore",
    )
    with pytest.raises(RuntimeError, match="runtime identity mismatch"):
        AnalysisRuntime(
            workspace_dir=workspace,
            session_id="r1_mismatch_session",
            owner_id="owner-b",
            question="identity restore",
        )


def test_legacy_runtime_snapshot_without_owner_is_not_created_unowned_after_migration(tmp_path: Path):
    first = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="r1_legacy",
        question="legacy compatibility",
    )
    assert first.owner_id is None
    assert first.task.owner_id is None
    restored = AnalysisRuntime(
        workspace_dir=first.workspace_dir,
        session_id="r1_legacy",
        owner_id="deployment-owner",
        question="legacy compatibility",
    )
    assert restored.task.owner_id == "deployment-owner"


def test_worker_job_carries_identity_but_rejects_invalid_owner():
    job = WorkerJob(
        session_id="r1_worker_session",
        owner_id="owner-a",
        code="print('ok')",
        workspace_dir="/tmp/deepanalyze-worker",
    )
    assert job.owner_id == "owner-a"
    with pytest.raises(ValueError):
        WorkerJob(
            session_id="r1_worker_session",
            owner_id="bad owner",
            code="print('bad')",
            workspace_dir="/tmp/deepanalyze-worker",
        )


def test_product_job_carries_owner(tmp_path: Path):
    prepared = build_product_worker_job(
        code="print('identity')",
        workspace_dir=str(tmp_path),
        session_id="r1_product_session",
        owner_id="owner-a",
    )
    assert prepared.job.owner_id == "owner-a"