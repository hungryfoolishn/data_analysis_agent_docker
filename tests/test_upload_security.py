from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from langgraph_langchain.api_server_langgraph import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_upload_sanitizes_path_traversal(client):
    response = client.post(
        "/workspace/upload",
        files={"file": ("../../evil.csv", b"value\n1\n")},
        data={"session_id": ""},
    )
    assert response.status_code == 200
    assert Path(response.json()["file_path"]).name.endswith("evil.csv")


def test_upload_rejects_unsupported_suffix(client):
    response = client.post(
        "/workspace/upload",
        files={"file": ("payload.exe", b"binary")},
        data={"session_id": ""},
    )
    assert response.status_code == 400
    assert "Unsupported upload type" in response.json()["detail"]


def test_upload_uses_unique_safe_filename(client):
    response = client.post(
        "/workspace/upload",
        files={"file": ("data.csv", b"value\n1\n")},
        data={"session_id": ""},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "data.csv"
    assert "/" not in Path(payload["file_path"]).name
    assert "\\" not in Path(payload["file_path"]).name
