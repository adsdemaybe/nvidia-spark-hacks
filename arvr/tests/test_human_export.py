"""Acceptance-gate tests for the human-layer LeRobot export
(`ar_datapipe.human_export` + `POST /spatial/episodes/{id}/export-human`).

Deliberately *no* `pytest.importorskip("pinocchio")` / `("mujoco")` at the
top, unlike every other export test in this suite. That absence is itself
the assertion: this path exists precisely because the robot hand it would
retarget onto does not exist yet, so it must run on a machine that has no
robot toolchain at all — the Windows dev box these tests run on being the
proof.

Only pyarrow is skipped for, because writing parquet without it is not
possible; on any machine that can read a LeRobot dataset it is present.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

import pyarrow.parquet as pq  # noqa: E402
from ar_backend.spatial_episodes import build_router  # noqa: E402
from ar_backend.spatial_store import HumanEpisodeStore  # noqa: E402
from ar_contracts import (  # noqa: E402
    HAND_JOINT_NAMES,
    HandFrame,
    HandJoint,
    HumanEpisode,
    HumanEpisodeMetadata,
)
from ar_datapipe.human_export import (  # noqa: E402
    ACTION_DIM,
    HAND_ORDER,
    MASK_DIM,
    N_JOINTS,
    WRIST_ORIENTATION_DIM,
    export_human_episode,
)
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

IDENTITY = (0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Builders — synthetic hands rather than the mock fixture, because every
# interesting case here (a dropped hand, an untracked joint, a joint that
# genuinely sits at the origin) is one the recorded fixture never contains.
# ---------------------------------------------------------------------------


def _joint(x: float, y: float, z: float, *, tracked: bool = True) -> HandJoint:
    return HandJoint(position_m=(x, y, z), orientation_xyzw=IDENTITY, tracked=tracked)


def _hand(
    side: str,
    timestamp_ns: int,
    *,
    omit: tuple[str, ...] = (),
    untracked: tuple[str, ...] = (),
    offset: float = 0.0,
    frame: str = "struct_world",
) -> HandFrame:
    """A full 26-joint hand whose joint i sits at (i/100 + offset, side, 0.5),
    so every joint has a distinct, checkable coordinate."""
    joints = {}
    for i, name in enumerate(HAND_JOINT_NAMES):
        if name in omit:
            continue
        joints[name] = _joint(
            i / 100.0 + offset,
            0.0 if side == "left" else 1.0,
            0.5,
            tracked=name not in untracked,
        )
    return HandFrame(
        timestamp_ns=timestamp_ns, source_device="openxr", hand=side, frame=frame,
        joints=joints,
    )


def _episode(frames: list[HandFrame], *, task_id: str = "sort_balls") -> HumanEpisode:
    return HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=str(uuid.uuid4()),
            task_id=task_id,
            asset_id="ball_01",
            hand_provider="openxr",
        ),
        hand_frames=frames,
    )


def _read(dataset_dir: Path, episode_index: int = 0):
    path = dataset_dir / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet"
    return pq.read_table(path).to_pylist()


def _block(row_values: list, hand_index: int, width: int) -> list:
    """One hand's slice out of a per-hand-concatenated column."""
    return row_values[hand_index * width : (hand_index + 1) * width]


# ---------------------------------------------------------------------------
# Alignment — one row is one instant, not one hand-frame
# ---------------------------------------------------------------------------


def test_both_hands_at_one_instant_collapse_into_one_row(tmp_path):
    frames = []
    for i, ts in enumerate((1_000, 2_000, 3_000)):
        frames.append(_hand("left", ts, offset=i))
        frames.append(_hand("right", ts, offset=i))
    result = export_human_episode(tmp_path / "ds", _episode(frames))

    assert result.n_rows == 3, "6 hand-frames at 3 instants must be 3 rows, not 6"
    assert result.action_dim == ACTION_DIM == 156

    rows = _read(tmp_path / "ds")
    assert len(rows) == 3
    # Every joint of both hands is real at every instant.
    for row in rows:
        assert row["joints_valid"] == [1.0] * MASK_DIM


def test_hands_land_in_their_declared_slots(tmp_path):
    """Left occupies the first half of the action vector, right the second —
    a swap here would be invisible in every aggregate check."""
    export_human_episode(
        tmp_path / "ds", _episode([_hand("left", 0), _hand("right", 0)])
    )
    row = _read(tmp_path / "ds")[0]
    assert HAND_ORDER == ("left", "right")
    left = _block(row["action"], 0, N_JOINTS * 3)
    right = _block(row["action"], 1, N_JOINTS * 3)
    # The builder puts y=0.0 on the left hand and y=1.0 on the right.
    assert left[1] == pytest.approx(0.0)
    assert right[1] == pytest.approx(1.0)


def test_frames_out_of_order_still_produce_ascending_rows(tmp_path):
    frames = [_hand("right", 3_000), _hand("right", 1_000), _hand("right", 2_000)]
    export_human_episode(tmp_path / "ds", _episode(frames))
    rows = _read(tmp_path / "ds")
    assert [r["timestamp"] for r in rows] == pytest.approx([0.0, 1e-6, 2e-6])
    assert [r["frame_index"] for r in rows] == [0, 1, 2]


def test_timestamps_are_seconds_relative_to_the_first_row(tmp_path):
    frames = [_hand("right", 5_000_000_000 + i * 100_000_000) for i in range(3)]
    export_human_episode(tmp_path / "ds", _episode(frames))
    rows = _read(tmp_path / "ds")
    assert [r["timestamp"] for r in rows] == pytest.approx([0.0, 0.1, 0.2])
    info = json.loads((tmp_path / "ds" / "meta" / "info.json").read_text())
    assert info["fps"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Missing data — the property this module exists to get right
# ---------------------------------------------------------------------------


def test_single_hand_episode_masks_the_absent_hand_instead_of_zeroing_it(tmp_path):
    export_human_episode(tmp_path / "ds", _episode([_hand("right", 0)]))
    row = _read(tmp_path / "ds")[0]

    left_mask = _block(row["joints_valid"], 0, N_JOINTS)
    right_mask = _block(row["joints_valid"], 1, N_JOINTS)
    assert left_mask == [0.0] * N_JOINTS
    assert right_mask == [1.0] * N_JOINTS

    left_action = _block(row["action"], 0, N_JOINTS * 3)
    assert all(math.isnan(v) for v in left_action), (
        "an absent hand must be NaN, never a plausible coordinate"
    )
    assert not any(v == 0.0 for v in left_action)


def test_untracked_joints_are_masked_not_zero_filled(tmp_path):
    """Both flavours of 'no data': the key is absent, and the key is present
    with tracked=False. They must be indistinguishable in the output."""
    frame = _hand("right", 0, omit=("thumb-tip",), untracked=("index-finger-tip",))
    export_human_episode(tmp_path / "ds", _episode([frame]))
    row = _read(tmp_path / "ds")[0]

    right_mask = _block(row["joints_valid"], 1, N_JOINTS)
    right_action = _block(row["action"], 1, N_JOINTS * 3)
    for name in ("thumb-tip", "index-finger-tip"):
        j = HAND_JOINT_NAMES.index(name)
        assert right_mask[j] == 0.0
        assert all(math.isnan(v) for v in right_action[j * 3 : j * 3 + 3])
    # Everything else survived untouched.
    assert sum(right_mask) == N_JOINTS - 2


def test_a_joint_genuinely_at_the_origin_is_not_read_as_missing(tmp_path):
    """The exact failure zero-filling would cause, from the other direction:
    a hand really at (0,0,0) must stay valid data."""
    frame = HandFrame(
        timestamp_ns=0,
        source_device="openxr",
        hand="right",
        joints={"wrist": _joint(0.0, 0.0, 0.0)},
    )
    export_human_episode(tmp_path / "ds", _episode([frame]))
    row = _read(tmp_path / "ds")[0]

    wrist_index = HAND_JOINT_NAMES.index("wrist")
    right_mask = _block(row["joints_valid"], 1, N_JOINTS)
    right_action = _block(row["action"], 1, N_JOINTS * 3)
    assert right_mask[wrist_index] == 1.0
    assert right_action[wrist_index * 3 : wrist_index * 3 + 3] == [0.0, 0.0, 0.0]


def test_a_hand_dropping_out_mid_recording(tmp_path):
    """Tracking loss is the normal case on a headset, not an edge case: the
    left hand leaves the camera's view partway through and comes back."""
    frames = [
        _hand("left", 0), _hand("right", 0),
        _hand("right", 1_000),
        _hand("right", 2_000),
        _hand("left", 3_000), _hand("right", 3_000),
    ]
    result = export_human_episode(tmp_path / "ds", _episode(frames))
    assert result.n_rows == 4

    rows = _read(tmp_path / "ds")
    left_present = [sum(_block(r["joints_valid"], 0, N_JOINTS)) > 0 for r in rows]
    assert left_present == [True, False, False, True]
    # The right hand is unaffected by the left's dropout — the two hands are
    # masked independently, not as one all-or-nothing block.
    assert all(sum(_block(r["joints_valid"], 1, N_JOINTS)) == N_JOINTS for r in rows)
    for row in rows[1:3]:
        assert all(math.isnan(v) for v in _block(row["action"], 0, N_JOINTS * 3))


# ---------------------------------------------------------------------------
# Wrist orientation and pinch aperture
# ---------------------------------------------------------------------------


def test_wrist_orientation_is_exported_per_hand_and_nan_when_absent(tmp_path):
    left = HandFrame(
        timestamp_ns=0, source_device="openxr", hand="left",
        joints={
            "wrist": HandJoint(
                position_m=(0.1, 0.2, 0.3), orientation_xyzw=(0.0, 0.0, 1.0, 0.0)
            )
        },
    )
    # Right hand tracked, but its wrist specifically is not.
    right = _hand("right", 0, omit=("wrist",))
    export_human_episode(tmp_path / "ds", _episode([left, right]))
    row = _read(tmp_path / "ds")[0]

    assert len(row["observation.wrist_orientation_xyzw"]) == WRIST_ORIENTATION_DIM == 8
    assert _block(row["observation.wrist_orientation_xyzw"], 0, 4) == pytest.approx(
        [0.0, 0.0, 1.0, 0.0]
    )
    assert all(
        math.isnan(v) for v in _block(row["observation.wrist_orientation_xyzw"], 1, 4)
    )


def test_pinch_aperture_is_measured_in_meters_and_nan_without_both_tips(tmp_path):
    joints = {
        "thumb-tip": _joint(0.0, 0.0, 0.0),
        "index-finger-tip": _joint(0.03, 0.04, 0.0),  # 5 cm away
    }
    right = HandFrame(
        timestamp_ns=0, source_device="openxr", hand="right", joints=joints
    )
    left = HandFrame(
        timestamp_ns=0, source_device="openxr", hand="left",
        joints={"thumb-tip": _joint(0.0, 0.0, 0.0)},  # index tip missing
    )
    export_human_episode(tmp_path / "ds", _episode([left, right]))
    row = _read(tmp_path / "ds")[0]

    assert row["observation.pinch_aperture_m"][1] == pytest.approx(0.05, abs=1e-6)
    assert math.isnan(row["observation.pinch_aperture_m"][0])


# ---------------------------------------------------------------------------
# Recordings the exporter must refuse
# ---------------------------------------------------------------------------


def test_empty_episode_is_rejected_clearly(tmp_path):
    with pytest.raises(ValueError, match="no hand frames"):
        export_human_episode(tmp_path / "ds", _episode([]))
    assert not (tmp_path / "ds").exists(), "a refused export must write nothing"


def test_episode_with_nothing_tracked_anywhere_is_rejected(tmp_path):
    frames = [
        HandFrame(timestamp_ns=t, source_device="openxr", hand="right", joints={})
        for t in (0, 1_000)
    ]
    with pytest.raises(ValueError, match="no joint was ever tracked"):
        export_human_episode(tmp_path / "ds", _episode(frames))


def test_two_frames_for_one_hand_at_one_instant_are_rejected(tmp_path):
    frames = [_hand("right", 0), _hand("right", 0, offset=9.0)]
    with pytest.raises(ValueError, match="share timestamp"):
        export_human_episode(tmp_path / "ds", _episode(frames))


def test_mixed_coordinate_frames_are_rejected(tmp_path):
    frames = [_hand("right", 0), _hand("right", 1_000, frame="device_frame")]
    with pytest.raises(ValueError, match="multiple coordinate frames"):
        export_human_episode(tmp_path / "ds", _episode(frames))


# ---------------------------------------------------------------------------
# On-disk shape: determinism, round-trip, metadata
# ---------------------------------------------------------------------------


def test_export_is_deterministic_byte_for_byte(tmp_path):
    episode = _episode([_hand("left", 0), _hand("right", 0), _hand("right", 1_000)])
    export_human_episode(tmp_path / "a", episode)
    export_human_episode(tmp_path / "b", episode)

    written = sorted(
        p.relative_to(tmp_path / "a")
        for p in (tmp_path / "a").rglob("*")
        if p.is_file()
    )
    assert written, "nothing was written"
    for rel in written:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel


def test_parquet_round_trips_with_the_declared_schema(tmp_path):
    episode = _episode([_hand("left", 0), _hand("right", 0)])
    result = export_human_episode(tmp_path / "ds", episode)

    table = pq.read_table(result.episode_path)
    assert table.num_rows == 1
    schema = {f.name: f.type for f in table.schema}
    assert set(schema) == {
        "frame_index",
        "timestamp",
        "action",
        "observation.state",
        "joints_valid",
        "observation.wrist_orientation_xyzw",
        "observation.pinch_aperture_m",
        "episode_index",
    }
    for name, width in (
        ("action", ACTION_DIM),
        ("observation.state", ACTION_DIM),
        ("joints_valid", MASK_DIM),
        ("observation.wrist_orientation_xyzw", WRIST_ORIENTATION_DIM),
        ("observation.pinch_aperture_m", len(HAND_ORDER)),
    ):
        assert schema[name].list_size == width, name

    row = table.to_pylist()[0]
    # observation.state is documented as a copy of action at this layer.
    assert row["observation.state"] == row["action"]


def test_meta_describes_the_layout_it_wrote(tmp_path):
    episode = _episode([_hand("right", 0), _hand("right", 1_000)], task_id="sort_balls")
    export_human_episode(tmp_path / "ds", episode, task="sort the red balls")
    meta = tmp_path / "ds" / "meta"

    info = json.loads((meta / "info.json").read_text())
    assert info["robot_type"] == "human_hands", "no robot was involved; do not claim one"
    assert info["struct_joint_order"] == list(HAND_JOINT_NAMES)
    assert info["struct_hand_order"] == ["left", "right"]
    assert info["struct_missing_value"] == "nan"
    assert info["features"]["action"]["shape"] == [ACTION_DIM]
    assert info["features"]["joints_valid"]["shape"] == [MASK_DIM]

    episodes = [json.loads(x) for x in (meta / "episodes.jsonl").read_text().splitlines()]
    assert episodes == [{"episode_index": 0, "tasks": ["sort the red balls"], "length": 2}]

    tasks = [json.loads(x) for x in (meta / "tasks.jsonl").read_text().splitlines()]
    assert tasks[0]["task"] == "sort the red balls"

    provenance = [
        json.loads(x) for x in (meta / "provenance.jsonl").read_text().splitlines()
    ]
    assert provenance[0]["layer"] == "human"
    assert provenance[0]["source_human_episode_id"] == episode.metadata.episode_id
    assert provenance[0]["hand_provider"] == "openxr"
    assert len(provenance[0]["human_episode_hash"]) == 64


def test_joint_order_matches_the_typescript_client(tmp_path):
    """The dataset column order is only meaningful if it is the same order the
    recorder used. `hands.ts` is the other end of that agreement; it is owned
    by another tree, so this asserts the invariant rather than editing it."""
    hands_ts_path = (
        Path(__file__).resolve().parents[1] / "packages" / "xr-web" / "src" / "hands.ts"
    )
    # Same guard test_sort_task.py uses: xr-web is a separately-owned tree and
    # may legitimately be absent from a checkout, and "the file is missing" is
    # not evidence the two lists disagree.
    if not hands_ts_path.exists():
        pytest.skip(f"{hands_ts_path} not present (xr-web tree absent)")
    hands_ts = hands_ts_path.read_text(encoding="utf-8")
    # Read the declaration itself, in source order, rather than asking "is
    # this name mentioned somewhere in the file" — a reordering is exactly the
    # drift that would silently mislabel every column, and a membership check
    # would not see it.
    array_body = hands_ts.split("HAND_JOINTS = [", 1)[1].split("]", 1)[0]
    palm = hands_ts.split("PALM_JOINT = ", 1)[1].split(";", 1)[0].strip().strip('"')
    listed = re.findall(r'"([a-z-]+)"', array_body) + [palm]
    assert listed == list(HAND_JOINT_NAMES), (
        "ar_contracts.HAND_JOINT_NAMES and hands.ts HAND_JOINTS+PALM_JOINT "
        "have drifted; the exported feature vector's meaning depends on them "
        "being the same list"
    )


def test_the_source_episode_is_not_mutated_by_exporting(tmp_path):
    """Spec section 6's core invariant, re-checked on the new exit path: raw
    human data survives export intact."""
    frames = [_hand("left", 0), _hand("right", 0)]
    episode = _episode(frames)
    before = [f.model_dump_json() for f in episode.hand_frames]
    export_human_episode(tmp_path / "ds", episode)
    assert [f.model_dump_json() for f in episode.hand_frames] == before
    assert episode.hand_frames is frames


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(build_router(HumanEpisodeStore(), tmp_path))
    return TestClient(app)


def _create_and_upload(client, frames: list[HandFrame], task_id: str = "sort_balls") -> str:
    episode_id = client.post(
        "/spatial/episodes",
        json={"task_id": task_id, "asset_id": "ball_01", "hand_provider": "openxr"},
    ).json()["episode_id"]
    resp = client.post(
        f"/spatial/episodes/{episode_id}/artifact",
        json={"hand_frames": [json.loads(f.model_dump_json()) for f in frames]},
    )
    assert resp.status_code == 200, resp.text
    return episode_id


def test_endpoint_writes_a_dataset_that_actually_exists(client, tmp_path):
    frames = [_hand("left", 0), _hand("right", 0), _hand("right", 1_000)]
    episode_id = _create_and_upload(client, frames)

    resp = client.post(f"/spatial/episodes/{episode_id}/export-human")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["episode_id"] == episode_id
    assert body["n_rows"] == 2
    assert body["action_dim"] == ACTION_DIM
    episode_path = Path(body["episode_path"])
    assert episode_path.is_file(), "the reported path must be a file that exists"
    assert pq.read_table(episode_path).num_rows == 2
    # Human datasets stay out of the robot exports' directory.
    assert Path(body["dataset_path"]) == tmp_path / "human" / "sort_balls"
    assert (Path(body["dataset_path"]) / "meta" / "info.json").is_file()


def test_endpoint_needs_no_robot_and_no_finish_call(client):
    """The whole point: no robot_id, no asset pose, no goal, no URDF, no
    Pinocchio — and no prior /finish, which on this platform cannot run."""
    episode_id = _create_and_upload(client, [_hand("right", 0)])
    assert client.post(f"/spatial/episodes/{episode_id}/export-human").status_code == 200


def test_endpoint_is_idempotent_under_a_retried_post(client):
    episode_id = _create_and_upload(client, [_hand("right", 0), _hand("right", 1_000)])
    first = client.post(f"/spatial/episodes/{episode_id}/export-human").json()
    second = client.post(f"/spatial/episodes/{episode_id}/export-human").json()
    assert first == second

    data_dir = Path(first["dataset_path"]) / "data" / "chunk-000"
    assert len(list(data_dir.glob("episode_*.parquet"))) == 1, (
        "a retry must not append the same demonstration a second time"
    )
    episodes = (
        Path(first["dataset_path"]) / "meta" / "episodes.jsonl"
    ).read_text().splitlines()
    assert len(episodes) == 1


def test_a_second_episode_of_the_same_task_appends_rather_than_overwrites(client):
    first_id = _create_and_upload(client, [_hand("right", 0)])
    second_id = _create_and_upload(client, [_hand("left", 0), _hand("left", 1_000)])
    first = client.post(f"/spatial/episodes/{first_id}/export-human").json()
    second = client.post(f"/spatial/episodes/{second_id}/export-human").json()

    assert first["dataset_path"] == second["dataset_path"]
    assert first["dataset_id"].endswith("episode_000000")
    assert second["dataset_id"].endswith("episode_000001")
    assert Path(first["episode_path"]).is_file()
    assert Path(second["episode_path"]).is_file()
    assert pq.read_table(second["episode_path"]).num_rows == 2


def test_endpoint_accepts_a_task_string_override(client):
    episode_id = _create_and_upload(client, [_hand("right", 0)])
    body = client.post(
        f"/spatial/episodes/{episode_id}/export-human",
        json={"task": "put the red ball in the red basket"},
    ).json()
    tasks = (Path(body["dataset_path"]) / "meta" / "tasks.jsonl").read_text()
    assert "put the red ball in the red basket" in tasks


def test_export_before_upload_is_409(client):
    episode_id = client.post(
        "/spatial/episodes", json={"task_id": "sort_balls", "asset_id": "ball_01"}
    ).json()["episode_id"]
    resp = client.post(f"/spatial/episodes/{episode_id}/export-human")
    assert resp.status_code == 409
    assert "upload an artifact" in resp.json()["detail"]


def test_export_of_unknown_episode_is_404(client):
    assert client.post("/spatial/episodes/nope/export-human").status_code == 404


def test_endpoint_reports_an_unusable_recording_as_422(client):
    """An upload of frames with no tracked joint at all is accepted by
    /artifact (it only checks ordering) and must fail here with a reason,
    not a 500."""
    frames = [
        HandFrame(timestamp_ns=t, source_device="openxr", hand="right", joints={})
        for t in (0, 1_000)
    ]
    episode_id = _create_and_upload(client, frames)
    resp = client.post(f"/spatial/episodes/{episode_id}/export-human")
    assert resp.status_code == 422
    assert "no joint was ever tracked" in resp.json()["detail"]
