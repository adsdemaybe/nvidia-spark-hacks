"""End-to-end: a recorded mug pickup becomes a shareable dataset.

This is the test that would have caught the failure the app actually hit. Every
piece of the export was individually covered and individually green while the
thing as a whole did not work, because nothing exercised the three calls the
client really makes, in order, against a real app.

It deliberately drives the HTTP API rather than the exporter functions -- the
client speaks HTTP, and a route that is missing, renamed, or mounted under the
wrong prefix is invisible to a test that imports the function directly. That is
precisely how a stale backend served `create` and `artifact` while answering
404 for `export-human`: the code on disk was correct the whole time.

The scenario mirrors the real task: a right hand reaches to the mug at
(0.26, 0, 0), closes on it, lifts it past the 8cm success threshold, and puts
it back down.
"""

from __future__ import annotations

import sqlite3

import pytest
from ar_backend.app import create_app
from fastapi.testclient import TestClient

# Matches mugPickupLayout.ts. If these drift, the dataset stops describing the
# task the app records.
MUG_START = (0.26, 0.0, 0.0)
LIFT_CLEARANCE_M = 0.08
TASK_ID = "pick_up_mug"
ASSET_ID = "mug_01"

FRAME_INTERVAL_NS = 33_000_000  # ~30fps, what the webcam provider produces


def _hand_frame(timestamp_ns: int, wrist_z: float) -> dict:
    """One instant of a right hand at the mug, `wrist_z` above the table.

    A handful of real joints rather than all 26: the export's job is to carry
    whatever was tracked, and a partially-tracked hand is the normal case for a
    single camera -- so this doubles as a check that missing joints stay
    missing instead of being filled in.
    """
    def joint(dx: float, dy: float, dz: float) -> dict:
        return {
            "position_m": [MUG_START[0] + dx, MUG_START[1] + dy, wrist_z + dz],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
        }

    return {
        "schema_version": "1.0",
        "timestamp_ns": timestamp_ns,
        "source_device": "webcam",
        "hand": "right",
        "frame": "struct_world",
        "joints": {
            "wrist": joint(0.0, -0.06, 0.0),
            "thumb-tip": joint(-0.03, 0.0, 0.02),
            "index-finger-tip": joint(0.03, 0.0, 0.02),
            "middle-finger-phalanx-proximal": joint(0.01, -0.02, 0.01),
        },
    }


def _demonstration() -> tuple[list[dict], list[dict], list[dict]]:
    """Reach, grasp, lift past the threshold, place, release."""
    heights = [0.0, 0.0, 0.02, 0.06, 0.12, 0.12, 0.06, 0.0]
    hand_frames = []
    object_states = []
    for i, height in enumerate(heights):
        t = FRAME_INTERVAL_NS * (i + 1)
        hand_frames.append(_hand_frame(t, wrist_z=height + 0.02))
        object_states.append(
            {
                "id": ASSET_ID,
                "position_m": [MUG_START[0], MUG_START[1], height],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "timestamp_ns": t,
            }
        )
    events = [
        {"type": "task_start", "timestamp_ns": FRAME_INTERVAL_NS},
        {"type": "grasp_start", "timestamp_ns": FRAME_INTERVAL_NS * 2, "object_id": ASSET_ID},
        {"type": "grasp_end", "timestamp_ns": FRAME_INTERVAL_NS * 7, "object_id": ASSET_ID},
        {"type": "task_finish", "timestamp_ns": FRAME_INTERVAL_NS * 8},
    ]
    return hand_frames, object_states, events


@pytest.fixture
def client(tmp_path):
    """A real app, writing to a temp dir so a test run never touches the
    committed database."""
    app = create_app(
        dataset_root=tmp_path / "lerobot",
        demo_db_path=tmp_path / "datasets" / "human_demos.sqlite",
    )
    with TestClient(app) as c:
        yield c


def _record_one_episode(client) -> dict:
    """The exact three calls `exportHumanEpisodeForTraining` makes, in order."""
    created = client.post(
        "/spatial/episodes",
        json={"task_id": TASK_ID, "asset_id": ASSET_ID, "hand_provider": "webcam"},
    )
    assert created.status_code == 200, created.text
    episode_id = created.json()["episode_id"]

    hand_frames, object_states, events = _demonstration()
    uploaded = client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": hand_frames, "object_states": object_states, "events": events},
    )
    assert uploaded.status_code == 200, uploaded.text

    exported = client.post(f"/spatial/episodes/{episode_id}/export-human", json={})
    assert exported.status_code == 200, exported.text
    return exported.json()


def test_the_three_calls_the_client_makes_all_succeed(client):
    # The regression guard for the 404. Individually-green pieces did not stop
    # the app from being unable to save a take.
    body = _record_one_episode(client)
    assert body["database_frames"] == 8
    assert body["database_episodes"] == 1


def test_the_export_route_exists_under_the_path_the_client_calls(client):
    # Named separately from the happy path because THIS is what was broken:
    # not the export logic, but whether the route was reachable at all.
    #
    # Read from the SERVED OpenAPI schema rather than by walking `app.routes`,
    # for two reasons. It is what a client can actually see, so it is the same
    # question the browser asks. And `app.routes` holds `_IncludedRouter`
    # wrappers in this FastAPI version rather than flat routes, so walking it
    # quietly finds nothing -- a route-existence check that always passes is
    # worse than no check at all.
    paths = client.get("/openapi.json").json()["paths"]
    spatial = {p for p in paths if p.startswith("/spatial/episodes")}
    assert "/spatial/episodes/{episode_id}/export-human" in spatial, sorted(spatial)
    assert "post" in paths["/spatial/episodes/{episode_id}/export-human"]


def test_the_recording_lands_in_a_committable_sqlite_file(client):
    body = _record_one_episode(client)
    db_path = body["database_path"]
    assert db_path.endswith("human_demos.sqlite")
    assert "data" not in db_path.replace("\\", "/").split("/")[:-1] or "datasets" in db_path

    # Opened with the standard library only -- the acceptance criterion for
    # handing this file to somebody else.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        episode = conn.execute("SELECT * FROM episodes").fetchone()
        n_frames = conn.execute("SELECT COUNT(*) FROM hand_frames").fetchone()[0]
    finally:
        conn.close()
    assert episode["task_id"] == TASK_ID
    assert episode["asset_id"] == ASSET_ID
    assert episode["hand_provider"] == "webcam"
    assert n_frames == 8


def test_the_lift_is_recoverable_from_the_database(client):
    # The dataset has to contain the demonstration, not just rows. If the mug's
    # height never crosses the success threshold in the stored data, whatever
    # was recorded is not the task -- and that is worth failing on, because a
    # dataset full of non-demonstrations trains a policy to do nothing.
    body = _record_one_episode(client)
    conn = sqlite3.connect(body["database_path"])
    try:
        peak = conn.execute(
            "SELECT MAX(z_m) FROM object_states WHERE object_id = ?", (ASSET_ID,)
        ).fetchone()[0]
        start = conn.execute(
            "SELECT z_m FROM object_states WHERE object_id = ? ORDER BY timestamp_ns LIMIT 1",
            (ASSET_ID,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert start == pytest.approx(0.0)
    assert peak >= LIFT_CLEARANCE_M


def test_untracked_joints_are_absent_end_to_end(client):
    # Carried all the way through the HTTP layer, not just asserted on the
    # exporter: zero-filling an occluded finger would teach a policy that it
    # teleports to the origin, and the origin is a real reachable point.
    body = _record_one_episode(client)
    conn = sqlite3.connect(body["database_path"])
    try:
        names = {r[0] for r in conn.execute("SELECT DISTINCT joint_name FROM hand_joints")}
    finally:
        conn.close()
    assert "wrist" in names
    assert "pinky-finger-tip" not in names


def test_several_episodes_accumulate_into_one_shareable_file(client):
    # What a collection session actually produces, and what gets pushed.
    for _ in range(3):
        body = _record_one_episode(client)
    assert body["database_episodes"] == 3

    conn = sqlite3.connect(body["database_path"])
    try:
        episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        frames = conn.execute("SELECT COUNT(*) FROM hand_frames").fetchone()[0]
    finally:
        conn.close()
    assert episodes == 3
    assert frames == 24


def test_a_take_with_no_frames_is_refused_not_silently_saved(client):
    created = client.post(
        "/spatial/episodes",
        json={"task_id": TASK_ID, "asset_id": ASSET_ID, "hand_provider": "webcam"},
    )
    episode_id = created.json()["episode_id"]
    # The artifact upload itself refuses an empty recording, so the episode
    # never reaches a state where it could be exported as a demonstration.
    empty = client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": [], "object_states": [], "events": []},
    )
    assert empty.status_code == 422
    assert client.post(f"/spatial/episodes/{episode_id}/export-human").status_code == 409


def test_exporting_twice_does_not_duplicate_the_demonstration(client):
    # The client retries on a failed save, and a doubled demonstration is
    # training data that is quietly wrong rather than obviously broken.
    created = client.post(
        "/spatial/episodes",
        json={"task_id": TASK_ID, "asset_id": ASSET_ID, "hand_provider": "webcam"},
    )
    episode_id = created.json()["episode_id"]
    hand_frames, object_states, events = _demonstration()
    client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": hand_frames, "object_states": object_states, "events": events},
    )
    first = client.post(f"/spatial/episodes/{episode_id}/export-human", json={}).json()
    second = client.post(f"/spatial/episodes/{episode_id}/export-human", json={}).json()

    conn = sqlite3.connect(second["database_path"])
    try:
        episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        frames = conn.execute("SELECT COUNT(*) FROM hand_frames").fetchone()[0]
    finally:
        conn.close()
    assert episodes == 1
    assert frames == 8
    assert first["episode_id"] == second["episode_id"]
