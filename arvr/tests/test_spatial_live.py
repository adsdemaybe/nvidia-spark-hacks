"""Acceptance-gate tests for the live retarget stream (Shadow Robot Spatial
Demonstration Pipeline spec section 47, Phase 7). Tests the router directly
against a minimal app -- not yet wired into ar_backend.create_app (that's
Phase 8-11, see arvr/STATE.md).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pinocchio")

from ar_backend.spatial_live import LiveSessionStore, build_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    store = LiveSessionStore()
    app = FastAPI()
    app.include_router(build_router(store))
    return TestClient(app)


def _hand_payload(x: float, t_ns: int) -> dict:
    return {
        "schema_version": "1.0",
        "timestamp_ns": t_ns,
        "source_device": "mock",
        "hand": "right",
        "joints": {
            "wrist": {
                "position_m": [x, 0.0, 0.6],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        },
    }


def test_create_session_returns_id(client):
    resp = client.post("/spatial/live")
    assert resp.status_code == 200
    assert "session_id" in resp.json()


def test_unknown_session_closes_the_socket(client):
    with pytest.raises(Exception):  # noqa: B017 - TestClient raises on 4004 close
        with client.websocket_connect("/spatial/live/does-not-exist"):
            pass


def test_live_stream_returns_robot_shadow_state(client):
    session_id = client.post("/spatial/live").json()["session_id"]
    with client.websocket_connect(f"/spatial/live/{session_id}") as ws:
        ws.send_text(json.dumps(_hand_payload(0.5, 0)))
        raw = ws.receive_text()
    state = json.loads(raw)
    assert state["robot_id"] == "so101"
    assert state["ik_status"] == "ok"
    assert len(state["joint_positions"]) == 6


def test_live_stream_warm_starts_across_messages(client):
    """Two nearby wrist targets should converge on similar joint solutions
    (warm-started IK), not independently-chosen ones -- otherwise the
    shadow robot would visibly jump for no reason during a smooth demo."""
    session_id = client.post("/spatial/live").json()["session_id"]
    with client.websocket_connect(f"/spatial/live/{session_id}") as ws:
        ws.send_text(json.dumps(_hand_payload(0.5, 0)))
        first = json.loads(ws.receive_text())
        ws.send_text(json.dumps(_hand_payload(0.51, 33_000_000)))
        second = json.loads(ws.receive_text())

    delta = max(
        abs(a - b)
        for a, b in zip(first["joint_positions"], second["joint_positions"], strict=True)
    )
    assert delta < 0.5
