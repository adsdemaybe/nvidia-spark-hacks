"""Acceptance-gate tests for the Spatial Episodes/Robots/Assets APIs
(Shadow Robot Spatial Demonstration Pipeline spec section 52, Phase 8).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pinocchio")
pytest.importorskip("mujoco")

from ar_backend import assets as assets_module  # noqa: E402
from ar_backend import robots as robots_module  # noqa: E402
from ar_backend.spatial_episodes import (
    build_router as build_spatial_episodes_router,  # noqa: E402
)
from ar_backend.spatial_store import HumanEpisodeStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from spatial_providers import MockHandProvider  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(build_spatial_episodes_router(HumanEpisodeStore(), tmp_path))
    app.include_router(robots_module.build_router())
    app.include_router(assets_module.build_router())
    return TestClient(app)


def _hand_frame_payloads() -> list[dict]:
    frames = list(MockHandProvider().stream())
    return [json.loads(f.model_dump_json()) for f in frames]


# ---------------------------------------------------------------------------
# Robots / Assets — plain file I/O, no Linux-only deps
# ---------------------------------------------------------------------------


def test_list_robots_includes_the_fixture_so101(client):
    resp = client.get("/robots")
    assert resp.status_code == 200
    robot_ids = [r["robot_id"] for r in resp.json()]
    assert "fixture_so101" in robot_ids


def test_get_known_robot_returns_manifest(client):
    resp = client.get("/robots/so101")
    assert resp.status_code == 200
    assert resp.json()["source"] == "fixture"


def test_get_unknown_robot_404s(client):
    assert client.get("/robots/not_a_real_robot").status_code == 404


def test_list_assets_includes_the_fixture_button(client):
    resp = client.get("/assets")
    assert resp.status_code == 200
    asset_ids = [a["asset_id"] for a in resp.json()]
    assert "button_01" in asset_ids


def test_get_asset_by_instance_id_resolves_to_the_fixture_dir(client):
    resp = client.get("/assets/button_01")
    assert resp.status_code == 200
    assert resp.json()["asset_id"] == "button_01"


def test_get_unknown_asset_404s(client):
    assert client.get("/assets/not_a_real_asset").status_code == 404


# ---------------------------------------------------------------------------
# Spatial Episodes — full lifecycle
# ---------------------------------------------------------------------------


def test_full_lifecycle_accepts(client):
    create_resp = client.post(
        "/spatial/episodes", json={"task_id": "press_button", "asset_id": "button_01"},
    )
    assert create_resp.status_code == 200
    episode_id = create_resp.json()["episode_id"]

    upload_resp = client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": _hand_frame_payloads()},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["n_frames"] == 90

    finish_resp = client.post(
        f"/spatial/episodes/{episode_id}/finish",
        json={
            "asset_world_pose": {
                "position_m": [0.4, 0.0, 0.53], "orientation_xyzw": [0, 0, 0, 1],
            },
            "goal_position_m": [0.2, -0.15, 0.7],
            "goal_tolerance_m": 0.1,
        },
    )
    assert finish_resp.status_code == 200
    body = finish_resp.json()
    assert body["status"] == "accepted", body.get("rejection_reason")
    assert body["dataset_id"] is not None

    status_resp = client.get(f"/spatial/episodes/{episode_id}")
    assert status_resp.status_code == 200
    assert status_resp.json() == finish_resp.json()


def test_finish_reports_rejection_reason_when_goal_is_wrong(client):
    episode_id = client.post(
        "/spatial/episodes", json={"task_id": "press_button", "asset_id": "button_01"},
    ).json()["episode_id"]
    client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": _hand_frame_payloads()},
    )
    resp = client.post(
        f"/spatial/episodes/{episode_id}/finish",
        json={
            "asset_world_pose": {
                "position_m": [0.4, 0.0, 0.53], "orientation_xyzw": [0, 0, 0, 1],
            },
            "goal_position_m": [10.0, 10.0, 10.0],
            "goal_tolerance_m": 0.01,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] is not None
    assert body["dataset_id"] is None


def test_finish_before_upload_is_rejected_with_409(client):
    episode_id = client.post(
        "/spatial/episodes", json={"task_id": "press_button", "asset_id": "button_01"},
    ).json()["episode_id"]
    resp = client.post(
        f"/spatial/episodes/{episode_id}/finish",
        json={
            "asset_world_pose": {
                "position_m": [0.4, 0.0, 0.53], "orientation_xyzw": [0, 0, 0, 1],
            },
            "goal_position_m": [0.2, -0.15, 0.7],
        },
    )
    assert resp.status_code == 409


def test_unknown_episode_id_404s(client):
    assert client.get("/spatial/episodes/does-not-exist").status_code == 404


def test_upload_rejects_non_monotonic_timestamps(client):
    episode_id = client.post(
        "/spatial/episodes", json={"task_id": "press_button", "asset_id": "button_01"},
    ).json()["episode_id"]
    frames = _hand_frame_payloads()
    frames[1]["timestamp_ns"], frames[0]["timestamp_ns"] = (
        frames[0]["timestamp_ns"],
        frames[1]["timestamp_ns"],
    )
    resp = client.post(
        f"/spatial/episodes/{episode_id}/artifact", json={"hand_frames": frames},
    )
    assert resp.status_code == 422


def test_raw_hand_frames_intact_after_finish(tmp_path):
    """The core invariant (spec section 6): raw human data must survive
    intact all the way through -- retargeting/verification/export must
    never mutate the stored artifact."""
    from ar_backend.spatial_store import HumanEpisodeStore

    store = HumanEpisodeStore()
    app = FastAPI()
    app.include_router(build_spatial_episodes_router(store, tmp_path))
    client = TestClient(app)

    episode_id = client.post(
        "/spatial/episodes", json={"task_id": "press_button", "asset_id": "button_01"},
    ).json()["episode_id"]
    payloads = _hand_frame_payloads()
    client.post(f"/spatial/episodes/{episode_id}/artifact", json={"hand_frames": payloads})

    finish_resp = client.post(
        f"/spatial/episodes/{episode_id}/finish",
        json={
            "asset_world_pose": {
                "position_m": [0.4, 0.0, 0.53], "orientation_xyzw": [0, 0, 0, 1],
            },
            "goal_position_m": [0.2, -0.15, 0.7],
            "goal_tolerance_m": 0.1,
        },
    )
    assert finish_resp.status_code == 200

    record = store.get(episode_id)
    assert len(record.hand_frames) == len(payloads)
    assert record.hand_frames[0].model_dump()["timestamp_ns"] == payloads[0]["timestamp_ns"]
    assert record.hand_frames[-1].model_dump()["timestamp_ns"] == payloads[-1]["timestamp_ns"]
