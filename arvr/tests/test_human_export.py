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
    ObjectState,
)
from ar_datapipe.human_export import (  # noqa: E402
    ACTION_DIM,
    HAND_ORDER,
    MASK_DIM,
    N_JOINTS,
    OBJECT_ORIENTATION_DIM,
    OBJECT_ORIENTATION_FEATURE,
    OBJECT_POSITION_DIM,
    OBJECT_POSITION_FEATURE,
    OBJECT_VALID_FEATURE,
    OBJECT_VELOCITY_DIM,
    OBJECT_VELOCITY_DT_FEATURE,
    OBJECT_VELOCITY_FEATURE,
    OBJECT_VELOCITY_VALID_FEATURE,
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


def _episode(
    frames: list[HandFrame],
    *,
    task_id: str = "sort_balls",
    objects: list[ObjectState] | None = None,
) -> HumanEpisode:
    return HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=str(uuid.uuid4()),
            task_id=task_id,
            asset_id="ball_01",
            hand_provider="openxr",
        ),
        hand_frames=frames,
        object_states=objects or [],
    )


def _obj(
    object_id: str,
    timestamp_ns: int | None,
    x: float,
    y: float = 0.0,
    z: float = 0.0,
    *,
    quat: tuple[float, float, float, float] = IDENTITY,
) -> ObjectState:
    return ObjectState(
        id=object_id,
        position_m=(x, y, z),
        orientation_xyzw=quat,
        timestamp_ns=timestamp_ns,
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
# Object trajectory — the thing a reaching policy actually has to observe
#
# Every test here is built on a hand grid of three instants 100 ms apart, so
# a 0.1 m displacement between consecutive samples is exactly 1.0 m/s and a
# wrong `dt` cannot hide inside a coincidence.
# ---------------------------------------------------------------------------

DECI_S = 100_000_000  # ns
GRID = (0, DECI_S, 2 * DECI_S)


def _hands_on_grid(instants=GRID) -> list[HandFrame]:
    return [_hand("right", ts, offset=i) for i, ts in enumerate(instants)]


def _info(dataset_dir: Path) -> dict:
    return json.loads((dataset_dir / "meta" / "info.json").read_text())


def _provenance(dataset_dir: Path, line: int = 0) -> dict:
    text = (dataset_dir / "meta" / "provenance.jsonl").read_text()
    return json.loads(text.splitlines()[line])


def _slot(row: dict, feature: str, index: int, width: int) -> list:
    """One object's slice out of a per-object-concatenated column."""
    return row[feature][index * width : (index + 1) * width]


def test_object_columns_carry_the_pose_at_that_instant(tmp_path):
    """The point of the whole feature: the row that holds the hand at time t
    also holds where the mug was at time t."""
    objects = [_obj("mug", ts, x=0.1 * i, y=0.5) for i, ts in enumerate(GRID)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    assert [r[OBJECT_VALID_FEATURE] for r in rows] == [[1.0], [1.0], [1.0]]
    for i, row in enumerate(rows):
        assert row[OBJECT_POSITION_FEATURE] == pytest.approx([0.1 * i, 0.5, 0.0])
        assert row[OBJECT_ORIENTATION_FEATURE] == pytest.approx([0.0, 0.0, 0.0, 1.0])


def test_object_identity_maps_to_column_slots_recorded_in_info_json(tmp_path):
    """Three objects, and which columns are whose must be readable from the
    dataset rather than inferred."""
    objects = []
    for i, ts in enumerate(GRID):
        objects.append(_obj("red_ball", ts, x=1.0 + i))
        objects.append(_obj("mug", ts, x=2.0 + i))
        objects.append(_obj("blue_basket", ts, x=3.0 + i))
    result = export_human_episode(
        tmp_path / "ds", _episode(_hands_on_grid(), objects=objects)
    )

    order = ["blue_basket", "mug", "red_ball"]
    assert result.object_order == tuple(order)
    assert _info(tmp_path / "ds")["struct_object_order"] == order

    row = _read(tmp_path / "ds")[0]
    # Slot i holds the object at info.json's index i, not the one that was
    # recorded first.
    expected_x = {"blue_basket": 3.0, "mug": 2.0, "red_ball": 1.0}
    for index, object_id in enumerate(order):
        got = _slot(row, OBJECT_POSITION_FEATURE, index, OBJECT_POSITION_DIM)
        assert got[0] == pytest.approx(expected_x[object_id]), object_id


def test_column_order_does_not_depend_on_recording_order(tmp_path):
    """The same scene recorded with the objects sampled in a different order
    must produce the same column assignment, or two episodes of one task
    cannot be compared row for row."""
    forward = [_obj("a_mug", 0, x=1.0), _obj("z_ball", 0, x=2.0)]
    reversed_order = [_obj("z_ball", 0, x=2.0), _obj("a_mug", 0, x=1.0)]
    a = export_human_episode(
        tmp_path / "a", _episode([_hand("right", 0)], objects=forward)
    )
    b = export_human_episode(
        tmp_path / "b", _episode([_hand("right", 0)], objects=reversed_order)
    )

    assert a.object_order == b.object_order == ("a_mug", "z_ball")
    assert (
        _read(tmp_path / "a")[0][OBJECT_POSITION_FEATURE]
        == _read(tmp_path / "b")[0][OBJECT_POSITION_FEATURE]
    )


def test_single_object_episode_still_uses_the_declared_ordering(tmp_path):
    result = export_human_episode(
        tmp_path / "ds",
        _episode(_hands_on_grid(), objects=[_obj("mug", ts, x=0.0) for ts in GRID]),
    )
    assert result.object_order == ("mug",)
    info = _info(tmp_path / "ds")
    assert info["struct_object_order"] == ["mug"]
    assert info["features"][OBJECT_POSITION_FEATURE]["shape"] == [OBJECT_POSITION_DIM]
    assert info["features"][OBJECT_VALID_FEATURE]["shape"] == [1]


def test_an_object_appearing_partway_through_is_invalid_before_it_appears(tmp_path):
    """The mug is only detected from the second instant. Its earlier rows must
    say 'unknown', not be back-filled from the first pose it was seen at."""
    objects = [_obj("mug", GRID[1], x=0.3), _obj("mug", GRID[2], x=0.4)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    assert [r[OBJECT_VALID_FEATURE][0] for r in rows] == [0.0, 1.0, 1.0]
    assert all(math.isnan(v) for v in rows[0][OBJECT_POSITION_FEATURE])
    assert all(math.isnan(v) for v in rows[0][OBJECT_ORIENTATION_FEATURE])
    assert not any(v == 0.0 for v in rows[0][OBJECT_POSITION_FEATURE]), (
        "an unobserved object must never look like an object at the origin"
    )
    # And the hand on that row is untouched by the object being absent.
    assert rows[0]["joints_valid"] == [0.0] * N_JOINTS + [1.0] * N_JOINTS


def test_object_sampled_on_a_subset_of_the_hand_instants(tmp_path):
    """Object tracking running at half the hand rate is the normal case, not
    an exotic one: the rows in between are invalid, not stale."""
    objects = [_obj("mug", GRID[0], x=0.0), _obj("mug", GRID[2], x=0.2)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    assert [r[OBJECT_VALID_FEATURE][0] for r in rows] == [1.0, 0.0, 1.0]
    assert all(math.isnan(v) for v in rows[1][OBJECT_POSITION_FEATURE]), (
        "a skipped sample must not carry the previous pose forward"
    )
    assert rows[2][OBJECT_POSITION_FEATURE][0] == pytest.approx(0.2)


def test_a_multi_object_episode_masks_each_object_independently(tmp_path):
    """One object dropping out must not blank the other — the whole reason
    validity is per object rather than one flag per row."""
    objects = [_obj("mug", ts, x=float(i)) for i, ts in enumerate(GRID)]
    objects.append(_obj("ball", GRID[1], x=9.0))
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    # sorted order -> slot 0 is "ball", slot 1 is "mug".
    assert _info(tmp_path / "ds")["struct_object_order"] == ["ball", "mug"]
    assert [r[OBJECT_VALID_FEATURE] for r in rows] == [
        [0.0, 1.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]
    assert _slot(rows[1], OBJECT_POSITION_FEATURE, 0, OBJECT_POSITION_DIM)[
        0
    ] == pytest.approx(9.0)
    assert all(
        math.isnan(v)
        for v in _slot(rows[0], OBJECT_POSITION_FEATURE, 0, OBJECT_POSITION_DIM)
    )


def test_an_object_genuinely_at_the_origin_is_not_read_as_missing(tmp_path):
    """The counterpart of the hand-at-the-origin test: (0,0,0) is a place."""
    export_human_episode(
        tmp_path / "ds",
        _episode([_hand("right", 0)], objects=[_obj("mug", 0, 0.0, 0.0, 0.0)]),
    )
    row = _read(tmp_path / "ds")[0]
    assert row[OBJECT_VALID_FEATURE] == [1.0]
    assert row[OBJECT_POSITION_FEATURE] == [0.0, 0.0, 0.0]


# --- velocity ---------------------------------------------------------------


def test_velocity_is_a_backward_difference_in_meters_per_second(tmp_path):
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    for row in rows[1:]:
        assert row[OBJECT_VELOCITY_VALID_FEATURE] == [1.0]
        assert row[OBJECT_VELOCITY_FEATURE] == pytest.approx([1.0, 0.0, 0.0], rel=1e-5)
        assert row[OBJECT_VELOCITY_DT_FEATURE] == pytest.approx([0.1], rel=1e-5)

    info = _info(tmp_path / "ds")
    assert info["struct_object_velocity_method"] == "backward_finite_difference"
    assert info["struct_object_velocity_validity_feature"] == OBJECT_VELOCITY_VALID_FEATURE


def test_velocity_on_the_first_measured_row_is_invalid_not_zero(tmp_path):
    """A zero here would assert the mug started at rest. Nothing was recorded
    that says so, and 'at rest' is exactly the claim a contact policy would
    act on."""
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    first = _read(tmp_path / "ds")[0]

    assert first[OBJECT_VALID_FEATURE] == [1.0], "the pose itself is real"
    assert first[OBJECT_VELOCITY_VALID_FEATURE] == [0.0]
    assert all(math.isnan(v) for v in first[OBJECT_VELOCITY_FEATURE])
    assert not any(v == 0.0 for v in first[OBJECT_VELOCITY_FEATURE])
    assert math.isnan(first[OBJECT_VELOCITY_DT_FEATURE][0])


def test_velocity_after_an_object_appears_midway_starts_invalid_too(tmp_path):
    """'First measured row' means the object's first, not the episode's."""
    objects = [_obj("mug", GRID[1], x=0.3), _obj("mug", GRID[2], x=0.4)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    assert [r[OBJECT_VELOCITY_VALID_FEATURE][0] for r in rows] == [0.0, 0.0, 1.0]
    assert rows[2][OBJECT_VELOCITY_FEATURE] == pytest.approx([1.0, 0.0, 0.0], rel=1e-5)


def test_velocity_across_a_gap_uses_the_real_interval_and_reports_it(tmp_path):
    """Differencing against 'the previous row' rather than 'the previous row
    the object was measured on' would double this velocity and nothing in the
    file would say so."""
    objects = [_obj("mug", GRID[0], x=0.0), _obj("mug", GRID[2], x=0.2)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    assert rows[2][OBJECT_VELOCITY_VALID_FEATURE] == [1.0]
    assert rows[2][OBJECT_VELOCITY_FEATURE] == pytest.approx([1.0, 0.0, 0.0], rel=1e-5)
    assert rows[2][OBJECT_VELOCITY_DT_FEATURE] == pytest.approx([0.2], rel=1e-5), (
        "the exported dt must be the real 200 ms interval, not the row spacing"
    )
    # The skipped row carries no velocity at all.
    assert rows[1][OBJECT_VELOCITY_VALID_FEATURE] == [0.0]
    assert all(math.isnan(v) for v in rows[1][OBJECT_VELOCITY_FEATURE])


def test_velocity_is_reproducible_from_the_exported_columns(tmp_path):
    """The self-consistency property the docstring promises: a consumer
    differencing position against timestamp gets the velocity column back."""
    objects = [_obj("mug", ts, x=0.1 * i, y=-0.05 * i) for i, ts in enumerate(GRID)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    rows = _read(tmp_path / "ds")

    for previous, row in zip(rows[:-1], rows[1:], strict=True):
        dt = row["timestamp"] - previous["timestamp"]
        recomputed = [
            (row[OBJECT_POSITION_FEATURE][k] - previous[OBJECT_POSITION_FEATURE][k]) / dt
            for k in range(OBJECT_VELOCITY_DIM)
        ]
        assert row[OBJECT_VELOCITY_FEATURE] == pytest.approx(recomputed, rel=1e-4)


def test_uneven_sampling_gets_its_own_dt_per_row(tmp_path):
    """The reason velocity is derived here at all: with a jittering frame
    interval a consumer that assumes a constant fps computes it wrong."""
    instants = (0, DECI_S, DECI_S + 50_000_000)
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(instants)]
    export_human_episode(
        tmp_path / "ds", _episode(_hands_on_grid(instants), objects=objects)
    )
    rows = _read(tmp_path / "ds")

    assert rows[1][OBJECT_VELOCITY_DT_FEATURE] == pytest.approx([0.1], rel=1e-5)
    assert rows[2][OBJECT_VELOCITY_DT_FEATURE] == pytest.approx([0.05], rel=1e-5)
    assert rows[1][OBJECT_VELOCITY_FEATURE] == pytest.approx([1.0, 0.0, 0.0], rel=1e-5)
    assert rows[2][OBJECT_VELOCITY_FEATURE] == pytest.approx([2.0, 0.0, 0.0], rel=1e-5)


# --- samples that cannot be placed on a row ---------------------------------


def test_object_samples_off_the_row_grid_are_dropped_and_counted(tmp_path):
    """An object pose stamped at an instant no hand frame exists at cannot be
    put on a row without inventing a hand pose for it. It is dropped — but the
    loss is a recorded number, not a silence."""
    objects = [
        _obj("mug", GRID[0], x=0.0),
        _obj("mug", GRID[0] + 7, x=99.0),  # 7 ns off: matches no hand instant
        _obj("mug", GRID[1], x=0.1),
    ]
    result = export_human_episode(
        tmp_path / "ds", _episode(_hands_on_grid(), objects=objects)
    )
    rows = _read(tmp_path / "ds")

    assert result.n_rows == 3, "object samples must never add or remove rows"
    assert [r[OBJECT_VALID_FEATURE][0] for r in rows] == [1.0, 1.0, 0.0]
    assert 99.0 not in rows[0][OBJECT_POSITION_FEATURE]
    assert _provenance(tmp_path / "ds")["n_object_samples_unaligned"] == 1
    assert _provenance(tmp_path / "ds")["n_object_states"] == 3


def test_untimestamped_object_states_cannot_be_aligned(tmp_path):
    """`ObjectState.timestamp_ns` is optional, and the button task's single
    end-of-episode snapshot leaves it None. A pose with no time has no row;
    it must not be smeared over the whole episode or pinned to the last row."""
    objects = [_obj("mug", None, x=5.0)]
    result = export_human_episode(
        tmp_path / "ds", _episode(_hands_on_grid(), objects=objects)
    )
    rows = _read(tmp_path / "ds")

    assert result.object_order == ("mug",), (
        "the object was recorded, so it still gets a column slot"
    )
    assert [r[OBJECT_VALID_FEATURE][0] for r in rows] == [0.0, 0.0, 0.0]
    assert _provenance(tmp_path / "ds")["n_object_samples_unaligned"] == 1


def test_two_different_poses_for_one_object_at_one_instant_are_rejected(tmp_path):
    objects = [_obj("mug", 0, x=1.0), _obj("mug", 0, x=2.0)]
    with pytest.raises(ValueError, match="two different poses"):
        export_human_episode(
            tmp_path / "ds", _episode([_hand("right", 0)], objects=objects)
        )


def test_an_identical_duplicate_object_sample_is_harmless(tmp_path):
    """A retransmitted sample says the same thing twice; there is nothing to
    choose between, so refusing the export would be pedantry."""
    objects = [_obj("mug", 0, x=1.0), _obj("mug", 0, x=1.0)]
    export_human_episode(tmp_path / "ds", _episode([_hand("right", 0)], objects=objects))
    row = _read(tmp_path / "ds")[0]
    assert row[OBJECT_VALID_FEATURE] == [1.0]
    assert row[OBJECT_POSITION_FEATURE] == pytest.approx([1.0, 0.0, 0.0])


# --- episodes with no objects, and the additive guarantee -------------------


def test_an_episode_with_no_objects_still_exports_without_object_columns(tmp_path):
    result = export_human_episode(tmp_path / "ds", _episode(_hands_on_grid()))
    assert result.n_rows == 3
    assert result.object_order == ()

    schema = {f.name for f in pq.read_table(result.episode_path).schema}
    for feature in (
        OBJECT_POSITION_FEATURE,
        OBJECT_ORIENTATION_FEATURE,
        OBJECT_VALID_FEATURE,
        OBJECT_VELOCITY_FEATURE,
        OBJECT_VELOCITY_DT_FEATURE,
        OBJECT_VELOCITY_VALID_FEATURE,
    ):
        assert feature not in schema, feature

    info = _info(tmp_path / "ds")
    assert info["struct_object_order"] == [], (
        "an empty list must still be written: it is what distinguishes "
        "'recorded no objects' from 'exported before objects existed'"
    )
    assert OBJECT_POSITION_FEATURE not in info["features"]


def test_adding_objects_changes_no_hand_column(tmp_path):
    """The additive guarantee, checked rather than asserted in prose: the same
    hands exported with and without objects produce identical hand columns in
    identical positions."""
    frames = [_hand("left", ts) for ts in GRID] + [_hand("right", ts) for ts in GRID]
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    plain = export_human_episode(tmp_path / "plain", _episode(list(frames)))
    withobj = export_human_episode(
        tmp_path / "withobj", _episode(list(frames), objects=objects)
    )

    hand_columns = [
        "frame_index",
        "timestamp",
        "action",
        "observation.state",
        "joints_valid",
        "observation.wrist_orientation_xyzw",
        "observation.pinch_aperture_m",
        "episode_index",
    ]
    plain_table = pq.read_table(plain.episode_path)
    withobj_table = pq.read_table(withobj.episode_path)

    assert [f.name for f in plain_table.schema] == hand_columns
    # The object columns are appended, so the hand columns keep their order as
    # well as their contents.
    assert [f.name for f in withobj_table.schema][:7] == hand_columns[:7]
    for name in hand_columns:
        assert plain_table.column(name).to_pylist() == withobj_table.column(
            name
        ).to_pylist(), name

    plain_info = _info(tmp_path / "plain")
    withobj_info = _info(tmp_path / "withobj")
    for name in ("action", "observation.state", "joints_valid"):
        assert plain_info["features"][name] == withobj_info["features"][name]


def test_object_export_is_deterministic_byte_for_byte(tmp_path):
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    objects += [_obj("ball", GRID[1], x=0.7)]
    episode = _episode(_hands_on_grid(), objects=objects)
    export_human_episode(tmp_path / "a", episode)
    export_human_episode(tmp_path / "b", episode)

    written = sorted(
        p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file()
    )
    assert written
    for rel in written:
        assert (tmp_path / "a" / rel).read_bytes() == (
            tmp_path / "b" / rel
        ).read_bytes(), rel


def test_object_columns_round_trip_with_the_declared_widths(tmp_path):
    objects = []
    for i, ts in enumerate(GRID):
        objects.append(_obj("mug", ts, x=float(i)))
        objects.append(_obj("ball", ts, x=float(i) + 10))
    result = export_human_episode(
        tmp_path / "ds", _episode(_hands_on_grid(), objects=objects)
    )

    table = pq.read_table(result.episode_path)
    schema = {f.name: f.type for f in table.schema}
    n = 2
    for name, width in (
        (OBJECT_POSITION_FEATURE, n * OBJECT_POSITION_DIM),
        (OBJECT_ORIENTATION_FEATURE, n * OBJECT_ORIENTATION_DIM),
        (OBJECT_VALID_FEATURE, n),
        (OBJECT_VELOCITY_FEATURE, n * OBJECT_VELOCITY_DIM),
        (OBJECT_VELOCITY_DT_FEATURE, n),
        (OBJECT_VELOCITY_VALID_FEATURE, n),
    ):
        assert schema[name].list_size == width, name
        # The declared shape and the real column must agree, or a consumer
        # sizing its buffers from info.json reads off the end.
        assert _info(tmp_path / "ds")["features"][name]["shape"] == [width], name


def test_object_orientation_is_exported_not_just_position(tmp_path):
    """A mug's rotation decides where it can be grasped; it is not recoverable
    from the position column."""
    tipped = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))
    export_human_episode(
        tmp_path / "ds",
        _episode([_hand("right", 0)], objects=[_obj("mug", 0, x=0.2, quat=tipped)]),
    )
    row = _read(tmp_path / "ds")[0]
    assert row[OBJECT_ORIENTATION_FEATURE] == pytest.approx(list(tipped), rel=1e-6)


def test_provenance_records_the_object_track_it_wrote(tmp_path):
    objects = [_obj("mug", GRID[0], x=0.0), _obj("ball", GRID[0], x=1.0)]
    export_human_episode(tmp_path / "ds", _episode(_hands_on_grid(), objects=objects))
    line = _provenance(tmp_path / "ds")

    assert line["object_order"] == ["ball", "mug"]
    assert line["n_object_states"] == 2
    assert line["n_object_samples_unaligned"] == 0
    assert len(line["object_states_hash"]) == 64
    assert len(line["human_episode_hash"]) == 64
    assert line["exporter_version"] == "human_export@2"


def test_the_object_states_hash_reacts_to_the_object_track(tmp_path):
    """A provenance hash that ignores half the exported data cannot tell two
    different datasets apart."""
    hands = _hands_on_grid()
    a = _episode(hands, objects=[_obj("mug", GRID[0], x=0.0)])
    b = _episode(hands, objects=[_obj("mug", GRID[0], x=1.0)])
    export_human_episode(tmp_path / "a", a)
    export_human_episode(tmp_path / "b", b)

    line_a = _provenance(tmp_path / "a")
    line_b = _provenance(tmp_path / "b")
    assert line_a["human_episode_hash"] == line_b["human_episode_hash"], (
        "the hands are identical, so the hand hash must not have moved"
    )
    assert line_a["object_states_hash"] != line_b["object_states_hash"]


def test_exporting_does_not_mutate_the_recorded_object_states(tmp_path):
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    episode = _episode(_hands_on_grid(), objects=objects)
    before = [s.model_dump_json() for s in episode.object_states]
    export_human_episode(tmp_path / "ds", episode)
    assert [s.model_dump_json() for s in episode.object_states] == before
    assert episode.object_states is objects


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


def _create_and_upload(
    client,
    frames: list[HandFrame],
    task_id: str = "sort_balls",
    objects: list[ObjectState] | None = None,
) -> str:
    episode_id = client.post(
        "/spatial/episodes",
        json={"task_id": task_id, "asset_id": "ball_01", "hand_provider": "openxr"},
    ).json()["episode_id"]
    payload = {"hand_frames": [json.loads(f.model_dump_json()) for f in frames]}
    if objects is not None:
        payload["object_states"] = [json.loads(s.model_dump_json()) for s in objects]
    resp = client.post(f"/spatial/episodes/{episode_id}/artifact", json=payload)
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


def test_endpoint_carries_the_uploaded_object_track_into_the_dataset(client):
    """The object poses the client uploaded must reach the parquet file. The
    route was already passing `object_states` through to the exporter and the
    exporter was throwing them away; this is the end-to-end proof it no
    longer does."""
    frames = _hands_on_grid()
    objects = [_obj("mug", ts, x=0.1 * i) for i, ts in enumerate(GRID)]
    episode_id = _create_and_upload(client, frames, objects=objects)

    body = client.post(f"/spatial/episodes/{episode_id}/export-human").json()
    rows = pq.read_table(body["episode_path"]).to_pylist()

    assert [r[OBJECT_VALID_FEATURE] for r in rows] == [[1.0], [1.0], [1.0]]
    assert rows[2][OBJECT_POSITION_FEATURE] == pytest.approx([0.2, 0.0, 0.0])
    assert rows[2][OBJECT_VELOCITY_FEATURE] == pytest.approx([1.0, 0.0, 0.0], rel=1e-5)
    info = json.loads((Path(body["dataset_path"]) / "meta" / "info.json").read_text())
    assert info["struct_object_order"] == ["mug"]


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
