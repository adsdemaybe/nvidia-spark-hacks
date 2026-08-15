"""Episodes + Scenes API tests — spec sections 36-37, 60 ("phone and
backend share same definitions" — enforced here by construction: every
request/response body is validated through ar_contracts models).

Same platform note as test_datapipe.py: /finish actually runs ar_datapipe,
so it needs Pinocchio/MuJoCo (Linux only, see STATE.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pinocchio")
pytest.importorskip("mujoco")

from ar_backend import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ar-xr"


@pytest.fixture
def client(tmp_path):
    app = create_app(scenes_dir=FIXTURES_DIR, dataset_root=tmp_path / "lerobot")
    return TestClient(app)


def _load_fixture_frames() -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURES_DIR / "sample_episode.jsonl").read_text().splitlines()
    ]


def _load_fixture_events() -> list[dict]:
    episode = json.loads((FIXTURES_DIR / "sample_episode.json").read_text())
    return episode["events"]


# ---------------------------------------------------------------------------
# Scenes API — spec section 37
# ---------------------------------------------------------------------------


def test_get_known_scene_returns_manifest(client):
    resp = client.get("/scenes/demo_room")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scene_id"] == "demo_room"
    assert any(a["id"] == "robot" for a in body["visual_assets"])


def test_get_unknown_scene_404s(client):
    resp = client.get("/scenes/does_not_exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Episodes API — spec section 36, full TEACH pipeline (section 42)
# ---------------------------------------------------------------------------


def test_full_episode_lifecycle_accepts(client):
    create_resp = client.post(
        "/xr/episodes",
        json={"task_id": "cube_to_bin", "source": {"device_type": "phone"}},
    )
    assert create_resp.status_code == 200
    episode_id = create_resp.json()["episode_id"]
    assert create_resp.json()["status"] == "created"

    upload_resp = client.post(
        f"/xr/episodes/{episode_id}/artifact",
        json={"frames": _load_fixture_frames(), "events": _load_fixture_events()},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["n_frames"] == len(_load_fixture_frames())

    finish_resp = client.post(
        f"/xr/episodes/{episode_id}/finish",
        json={"goal_position_m": [0.60, 0.30, 0.55]},
    )
    assert finish_resp.status_code == 200
    body = finish_resp.json()
    assert body["status"] == "accepted"
    assert body["dataset_id"] is not None
    assert body["tracking_error_m"] < 0.01

    status_resp = client.get(f"/xr/episodes/{episode_id}")
    assert status_resp.status_code == 200
    assert status_resp.json() == finish_resp.json()


def test_finish_reports_rejection_reason_when_goal_is_wrong(client):
    episode_id = client.post(
        "/xr/episodes",
        json={"task_id": "cube_to_bin", "source": {"device_type": "phone"}},
    ).json()["episode_id"]
    client.post(
        f"/xr/episodes/{episode_id}/artifact",
        json={"frames": _load_fixture_frames(), "events": _load_fixture_events()},
    )
    resp = client.post(
        f"/xr/episodes/{episode_id}/finish",
        json={"goal_position_m": [5.0, 5.0, 5.0]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] is not None
    assert body["dataset_id"] is None


def test_finish_before_upload_is_rejected_with_409(client):
    episode_id = client.post(
        "/xr/episodes",
        json={"task_id": "cube_to_bin", "source": {"device_type": "phone"}},
    ).json()["episode_id"]
    resp = client.post(
        f"/xr/episodes/{episode_id}/finish",
        json={"goal_position_m": [0.6, 0.3, 0.55]},
    )
    assert resp.status_code == 409


def test_unknown_episode_id_404s(client):
    assert client.get("/xr/episodes/does-not-exist").status_code == 404


def test_upload_rejects_invalid_frame_payload(client):
    episode_id = client.post(
        "/xr/episodes",
        json={"task_id": "cube_to_bin", "source": {"device_type": "phone"}},
    ).json()["episode_id"]
    resp = client.post(
        f"/xr/episodes/{episode_id}/artifact",
        json={"frames": [{"timestamp_ns": 0, "position_m": [0, 0, 0]}]},  # missing fields
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Follow API — spec section 39, acceptance gate section 64
# ---------------------------------------------------------------------------


def _follow_state(t_ns: int, human_x: float, target_x: float) -> dict:
    return {
        "timestamp_ns": t_ns,
        "human_pose": {"position_m": [human_x, 0.0, 0.0], "orientation_xyzw": [0, 0, 0, 1]},
        "desired_follow_distance_m": 1.5,
        "follow_target": {"position_m": [target_x, 0.0, 0.0]},
    }


def test_follow_session_create_returns_id(client):
    resp = client.post("/xr/follow")
    assert resp.status_code == 200
    assert "session_id" in resp.json()


def test_unknown_follow_session_closes_the_socket(client):
    with pytest.raises(Exception):  # noqa: B017 - TestClient raises on 4004 close
        with client.websocket_connect("/xr/follow/does-not-exist"):
            pass


def test_follow_stream_moves_robot_base_toward_target(client):
    session_id = client.post("/xr/follow").json()["session_id"]
    with client.websocket_connect(f"/xr/follow/{session_id}") as ws:
        ws.send_text(json.dumps(_follow_state(0, human_x=5.0, target_x=3.5)))
        first = json.loads(ws.receive_text())
        robot = next(o for o in first["objects"] if o["id"] == "robot_base")
        start_x = robot["position_m"][0]

        # 2s later, same target -> should have moved closer (capped speed).
        ws.send_text(json.dumps(_follow_state(2_000_000_000, human_x=5.0, target_x=3.5)))
        second = json.loads(ws.receive_text())
        robot2 = next(o for o in second["objects"] if o["id"] == "robot_base")
        end_x = robot2["position_m"][0]

    assert end_x > start_x  # moved toward 3.5 from the initial (0.15, ...)
    assert end_x <= 3.5 + 1e-6  # never overshoots the target


def test_follow_stream_stops_moving_when_client_stops_sending(client):
    """spec section 64: STOP immediately halts target generation. There's no
    server-side timer driving motion — if the client stops sending, the
    chase state simply stops updating (no polling loop, no motion)."""
    session_id = client.post("/xr/follow").json()["session_id"]
    with client.websocket_connect(f"/xr/follow/{session_id}") as ws:
        ws.send_text(json.dumps(_follow_state(0, human_x=5.0, target_x=3.5)))
        first = json.loads(ws.receive_text())
        pos1 = next(o for o in first["objects"] if o["id"] == "robot_base")["position_m"]

    # New connection, same session: state persisted, nothing moved since
    # (no message was sent in between).
    with client.websocket_connect(f"/xr/follow/{session_id}") as ws:
        ws.send_text(json.dumps(_follow_state(0, human_x=5.0, target_x=3.5)))
        second = json.loads(ws.receive_text())
        pos2 = next(o for o in second["objects"] if o["id"] == "robot_base")["position_m"]

    assert pos1 == pos2


# ---------------------------------------------------------------------------
# Corrections API — spec section 40, DoD section 70 ("can be replayed or
# verified")
# ---------------------------------------------------------------------------


def _correction_event(corrected_position_m: list[float]) -> dict:
    return {
        "task_id": "cube_to_bin",
        "timestamp_ns": 1_700_000_000_000_000_000,
        "original_target": {"position_m": [0.4, 0.2, 0.5]},
        "corrected_target": {"position_m": corrected_position_m},
        "reason": "collision_avoidance",
    }


def test_correction_is_stored_and_verified_reachable(client):
    resp = client.post("/xr/corrections", json=_correction_event([0.3, -0.1, 0.5]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["event"]["reason"] == "collision_avoidance"
    assert body["verification"]["checked"] is True
    assert body["verification"]["reachable"] is True


def test_correction_to_an_unreachable_target_is_flagged(client):
    resp = client.post("/xr/corrections", json=_correction_event([5.0, 5.0, 5.0]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"]["checked"] is True
    assert body["verification"]["reachable"] is False
    assert body["verification"]["reason"] is not None


def test_corrections_list_returns_stored_events(client):
    client.post("/xr/corrections", json=_correction_event([0.3, -0.1, 0.5]))
    client.post("/xr/corrections", json=_correction_event([0.35, -0.05, 0.5]))

    resp = client.get("/xr/corrections")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
