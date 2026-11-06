from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from langgraph_langchain import api_server_langgraph as api_module
from langgraph_langchain.api_server_langgraph import _validate_upload_file


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "development")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", False)
    api_module._rate_limit_counters.clear()
    with TestClient(api_module.app) as test_client:
        yield test_client


def _headers():
    return {"Authorization": "Bearer test-token"}


def _unique_session() -> str:
    import uuid

    return f"security_{uuid.uuid4().hex}"


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_missing_token_is_rejected(client, monkeypatch):
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "test-token")
    response = client.get("/workspace/files", params={"session_id": "missing"})
    assert response.status_code == 401


def test_empty_token_does_not_bypass_auth(client, monkeypatch):
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "development")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", False)
    response = client.get("/health")
    assert response.status_code == 200
    response = client.get(
        "/workspace/files", params={"session_id": "any"}, headers={}
    )
    assert response.status_code == 503


def test_explicit_development_insecure_mode_can_bypass(client, monkeypatch):
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "development")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", True)
    response = client.get("/workspace/files", params={"session_id": "any"})
    assert response.status_code == 404


def test_invalid_and_valid_tokens(client):
    session_id = _unique_session()
    client.post(
        "/workspace/upload",
        files={"file": ("data.csv", "value\n1\n")},
        data={"session_id": session_id},
        headers=_headers(),
    )
    invalid = client.get(
        "/workspace/files",
        params={"session_id": session_id},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert invalid.status_code == 401

    response = client.get(
        "/workspace/files", params={"session_id": session_id}, headers=_headers()
    )
    assert response.status_code == 200
    client.delete(f"/sessions/{session_id}", headers=_headers())


def test_workspace_listing_requires_session(client):
    response = client.get("/workspace/files", headers=_headers())
    assert response.status_code == 422
    response = client.get(
        "/workspace/files", params={"session_id": "missing"}, headers=_headers()
    )
    assert response.status_code == 404


def test_session_upload_and_scoped_download(client):
    session_id = _unique_session()
    upload = client.post(
        "/workspace/upload",
        files={"file": ("data.csv", "value\n1\n")},
        data={"session_id": session_id},
        headers=_headers(),
    )
    assert upload.status_code == 200
    payload = upload.json()
    relative = f"{session_id}/{payload['filename']}"

    listing = client.get(
        "/workspace/files", params={"session_id": session_id}, headers=_headers()
    )
    assert listing.status_code == 200
    assert any(item["filename"] == payload["filename"] for item in listing.json()["files"])

    download = client.get(
        f"/workspace/files/{relative}",
        params={"session_id": session_id},
        headers=_headers(),
    )
    assert download.status_code == 200
    assert download.text == "value\n1\n"

    other_session = _unique_session()
    cross = client.get(
        f"/workspace/files/{relative}",
        params={"session_id": other_session},
        headers=_headers(),
    )
    assert cross.status_code == 404

    client.delete(f"/sessions/{session_id}?purge=true", headers=_headers())


def test_download_rejects_path_traversal(client):
    session_id = _unique_session()
    client.get(
        "/workspace/files", params={"session_id": session_id}, headers=_headers()
    )
    for target in [
        "../.sessions.json",
        f"../{session_id}/.analysis_runtime.json",
        "/etc/passwd",
        f"\\\\server\\share\\secret.csv",
    ]:
        response = client.get(
            f"/workspace/files/{target}",
            params={"session_id": session_id},
            headers=_headers(),
        )
        assert response.status_code in {400, 404}
    client.delete(f"/sessions/{session_id}", headers=_headers())


def test_symlink_escape_is_denied(client, tmp_path):
    session_id = _unique_session()
    client.get(
        "/workspace/files", params={"session_id": session_id}, headers=_headers()
    )
    outside = tmp_path / "outside.csv"
    outside.write_text("secret", encoding="utf-8")
    workspace = api_module.get_session_workspace(session_id)
    link = workspace / "outside.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")
    response = client.get(
        f"/workspace/files/{session_id}/outside.csv",
        params={"session_id": session_id},
        headers=_headers(),
    )
    assert response.status_code == 404
    client.delete(f"/sessions/{session_id}?purge=true", headers=_headers())


def test_oversized_upload_is_streamed_and_rejected(client, monkeypatch):
    session_id = _unique_session()
    client.get(
        "/workspace/files", params={"session_id": session_id}, headers=_headers()
    )
    monkeypatch.setattr(api_module, "MAX_UPLOAD_SIZE_MB", 0)
    response = client.post(
        "/workspace/upload",
        files={"file": ("large.csv", "value\n1\n")},
        data={"session_id": session_id},
        headers=_headers(),
    )
    assert response.status_code == 413
    workspace = api_module.get_session_workspace(session_id)
    assert not list(workspace.glob(".upload_*.tmp"))
    client.delete(f"/sessions/{session_id}?purge=true", headers=_headers())


def test_disguised_file_type_is_rejected(client):
    session_id = _unique_session()
    response = client.post(
        "/workspace/upload",
        files={"file": ("payload.parquet", "not parquet")},
        data={"session_id": session_id},
        headers=_headers(),
    )
    assert response.status_code == 400
    workspace = api_module.get_session_workspace(session_id)
    assert not list(workspace.glob(".upload_*.tmp"))
    client.delete(f"/sessions/{session_id}?purge=true", headers=_headers())


def test_production_requires_token(monkeypatch):
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", False)
    with pytest.raises(RuntimeError, match="API_AUTH_TOKEN is required"):
        api_module._validate_security_configuration()


def test_production_rejects_insecure_mode(monkeypatch):
    monkeypatch.setattr(api_module, "API_ENVIRONMENT", "production")
    monkeypatch.setattr(api_module, "API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(api_module, "API_ALLOW_INSECURE_AUTH", True)
    with pytest.raises(RuntimeError, match="API_ALLOW_INSECURE_AUTH"):
        api_module._validate_security_configuration()


def _write_parquet(path):
    pd.DataFrame({"value": [1, 2]}).to_parquet(path, index=False)


def test_parquet_upload_validates_header_and_footer(tmp_path):
    path = tmp_path / "valid.parquet"
    _write_parquet(path)

    _validate_upload_file(path, ".parquet")


def test_parquet_upload_rejects_wrong_footer(tmp_path):
    path = tmp_path / "bad_footer.parquet"
    path.write_bytes(b"PAR1" + b"x" * 8 + b"NOPE")

    with pytest.raises(Exception, match="Invalid Parquet structure"):
        _validate_upload_file(path, ".parquet")


def test_parquet_upload_rejects_truncated_file(tmp_path):
    path = tmp_path / "truncated.parquet"
    path.write_bytes(b"PAR1")

    with pytest.raises(Exception, match="truncated"):
        _validate_upload_file(path, ".parquet")


def test_parquet_upload_rejects_text_disguise(tmp_path):
    path = tmp_path / "text.parquet"
    path.write_bytes(b"not parquet")

    with pytest.raises(Exception, match="Invalid Parquet structure"):
        _validate_upload_file(path, ".parquet")


def test_valid_parquet_upload_endpoint(client, tmp_path):
    session_id = _unique_session()
    path = tmp_path / "valid.parquet"
    _write_parquet(path)
    response = client.post(
        "/workspace/upload",
        files={"file": ("valid.parquet", path.read_bytes())},
        data={"session_id": session_id},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert Path(response.json()["file_path"]).suffix == ".parquet"
    client.delete(f"/sessions/{session_id}?purge=true", headers=_headers())
