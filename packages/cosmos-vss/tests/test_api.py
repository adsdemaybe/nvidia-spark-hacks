from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COSMOS_VSS_BACKEND", "mock")
    monkeypatch.setenv("COSMOS_VSS_ARTIFACT_DIR", str(tmp_path / "artifacts" / "semantic"))

    import cosmos_vss.app as app_module

    importlib.reload(app_module)  # rebuild module-level config/analyzer against the patched env
    return TestClient(app_module.app)


def test_health_reports_mock_backend_ready(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "cosmos-vss-sidecar"
    assert body["backend"] == "mock"
    assert body["backend_ready"] is True


def test_analyze_video_returns_valid_episode_and_persists_artifact(client: TestClient, tmp_path: Path):
    video_bytes = b"fake mp4 bytes for the mock backend"
    resp = client.post(
        "/analyze/video",
        files={"file": ("demo.mp4", video_bytes, "video/mp4")},
        data={"episode_id": "episode_007"},
    )
    assert resp.status_code == 200
    episode = resp.json()
    assert episode["episode_id"] == "episode_007"
    assert episode["backend"] == "mock"
    assert episode["task_type"]

    get_resp = client.get(f"/semantic/{episode['semantic_id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["semantic_id"] == episode["semantic_id"]


def test_get_unknown_semantic_id_returns_404(client: TestClient):
    resp = client.get("/semantic/sem_doesnotexist")
    assert resp.status_code == 404


def test_analyze_vss_asset_rejected_when_backend_is_not_vss(client: TestClient):
    resp = client.post("/analyze/vss", json={"file_id": "abc123", "episode_id": "episode_001"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"]
