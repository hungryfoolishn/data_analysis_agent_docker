from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from langgraph_langchain import api_server_langgraph as api_module


@pytest.fixture()
def client(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api_module, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(api_module, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "development")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", False)
    api_module._rate_limit_counters.clear()
    api_module.SESSIONS.clear()
    with TestClient(api_module.app) as test_client:
        yield test_client


def _headers():
    return {"Authorization": "Bearer test-token"}


def _session_id() -> str:
    return "r0b_" + uuid.uuid4().hex[:24]


def _register_session(session_id: str, owner_id: str, workspace: Path):
    api_module.SESSIONS[session_id] = api_module._serialize_session(
        {
            "files": [],
            "artifacts": [],
            "owner_id": owner_id,
            "created_at": datetime.now().isoformat(),
            "last_accessed_at": datetime.now().isoformat(),
            "workspace": str(workspace),
        }
    )


def test_session_id_policy_rejects_traversal_and_short_ids():
    for value in ["../../outside", "/absolute", "a/b", "short", "", "..", "x" * 65]:
        with pytest.raises(api_module.SessionIdentityError):
            api_module.validate_session_id(value)
    assert api_module.validate_session_id("Valid_Session-1234567890") == "Valid_Session-1234567890"


def test_workspace_root_rejects_symlink_session(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api_module, "WORKSPACE_DIR", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(api_module.SessionIdentityError):
        api_module.get_session_workspace("../../outside")
    link = tmp_path / ("link_" + uuid.uuid4().hex[:16])
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported")
    with pytest.raises(Exception):
        api_module.get_session_workspace(link.name)


def test_cross_owner_session_is_hidden_and_not_mutated(client):
    session_id = _session_id()
    workspace = api_module.get_session_workspace(session_id)
    _register_session(session_id, "owner-a", workspace)

    response = client.get("/workspace/files", params={"session_id": session_id}, headers=_headers())
    assert response.status_code == 404
    assert session_id in api_module.SESSIONS

    upload = client.post(
        "/workspace/upload",
        files={"file": ("data.csv", "value\n1\n")},
        data={"session_id": session_id},
        headers=_headers(),
    )
    assert upload.status_code == 404
    assert not list(workspace.glob("*data.csv"))

    delete = client.delete(f"/sessions/{session_id}", headers=_headers())
    assert delete.status_code == 404
    assert session_id in api_module.SESSIONS

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("DEEPANALYZE_API_OWNER_ID", "owner-a")
        authorized = client.get(
            "/workspace/files", params={"session_id": session_id}, headers=_headers()
        )
    assert authorized.status_code == 200


def test_invalid_session_id_is_rejected_not_internal_error(client):
    response = client.get(
        "/workspace/files", params={"session_id": "../../outside"}, headers=_headers()
    )
    assert response.status_code in {400, 404}
    response = client.get("/workspace/files", params={"session_id": "short"}, headers=_headers())
    assert response.status_code in {400, 404}


def test_download_blocks_traversal_and_symlink_escape(client, tmp_path: Path):
    session_id = _session_id()
    workspace = api_module.get_session_workspace(session_id)
    _register_session(session_id, api_module.default_owner_id(), workspace)
    outside = tmp_path / "outside.csv"
    outside.write_text("secret", encoding="utf-8")

    for target in [
        "../.sessions.json",
        "/etc/passwd",
        "..%2f..%2f.sessions.json",
        "\\\\server\\share\\secret.csv",
    ]:
        response = client.get(
            f"/workspace/files/{target}",
            params={"session_id": session_id},
            headers=_headers(),
        )
        assert response.status_code in {400, 404}

    try:
        (workspace / "outside.csv").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported")
    response = client.get(
        f"/workspace/files/{session_id}/outside.csv",
        params={"session_id": session_id},
        headers=_headers(),
    )
    assert response.status_code == 404


def test_data_file_resolution_is_session_scoped(tmp_path: Path):
    session_id = _session_id()
    workspace = api_module.get_session_workspace(session_id)
    session = api_module._serialize_session(
        {"workspace": str(workspace), "files": [], "artifacts": []}
    )
    outside = tmp_path / "outside.csv"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as absolute:
        api_module._resolve_data_file(session, str(outside))
    assert absolute.value.status_code == 404
    with pytest.raises(HTTPException) as traversal:
        api_module._resolve_data_file(session, "../outside.csv")
    assert traversal.value.status_code == 404


def test_unsafe_persisted_session_is_skipped_without_external_delete(monkeypatch, tmp_path: Path):
    sessions_file = tmp_path / ".sessions.json"
    outside = tmp_path / "outside-important"
    outside.mkdir()
    sessions_file.write_text(
        json.dumps(
            {
                "../../evil": {"workspace": str(outside), "created_at": "2026-01-01T00:00:00"},
                "short": {"workspace": str(outside), "created_at": "2026-01-01T00:00:00"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_module, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(api_module, "_SESSIONS_FILE", sessions_file)
    api_module.SESSIONS.clear()
    api_module._load_sessions()
    assert api_module.SESSIONS == {}
    assert outside.exists()


def _write_run_snapshot(workspace: Path, session_id: str) -> str:
    run_id = "run_" + uuid.uuid4().hex
    snapshot = {
        "task": {"session_id": session_id, "question": "security check", "task_id": "task_1"},
        "run": {
            "run_id": run_id,
            "task_id": "task_1",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        },
    }
    history_dir = workspace / ".analysis_runs"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{run_id}.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    return run_id


def test_cross_owner_run_access_is_denied(client):
    session_id = _session_id()
    workspace = api_module.get_session_workspace(session_id)
    _register_session(session_id, "owner-a", workspace)
    run_id = _write_run_snapshot(workspace, session_id)

    response = client.get(f"/analysis/runs/{run_id}", headers=_headers())
    assert response.status_code == 404

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("DEEPANALYZE_API_OWNER_ID", "owner-a")
        authorized = client.get(f"/analysis/runs/{run_id}", headers=_headers())
    assert authorized.status_code == 200
    assert authorized.json()["run"]["run_id"] == run_id