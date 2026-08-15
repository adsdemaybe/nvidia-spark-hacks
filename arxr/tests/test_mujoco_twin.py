"""Acceptance gate for the local physics twin (STRUCT_2.md 43, 65).

MuJoCo is not the authoritative twin -- that stays Isaac/OpenUSD on the Spark
(STRUCT_2.md 52, 53). What this buys is that the object motion a human sees in
the client is produced by gravity and contact rather than by a sine wave, on a
machine with no Spark access.

These tests assert physics, not shape. A twin that merely emits well-formed
TwinState proves nothing.
"""
from __future__ import annotations

import pytest
from arxr.core.schemas import TwinState
from arxr.sim.twin import MujocoTwinSource

TABLE_TOP_M = 0.75
CUBE_HALF_M = 0.03


def test_emits_a_valid_twin_state():
    source = MujocoTwinSource(scene_id="demo_room")

    state = source.state()

    assert TwinState.model_validate(state.model_dump()) == state
    assert state.scene_id == "demo_room"


def test_reports_one_position_per_arm_joint():
    source = MujocoTwinSource()

    assert len(source.state().robot.joint_positions) == source.joint_count
    assert source.joint_count >= 6


def test_a_cube_dropped_above_the_table_falls():
    source = MujocoTwinSource(cube_height_m=1.10)

    start = source.cube_position()[2]
    for _ in range(15):
        source.step()
    later = source.cube_position()[2]

    assert later < start - 0.05, "gravity did not act on the cube"


def test_the_cube_comes_to_rest_on_the_table_rather_than_falling_through():
    """Contact, not just gravity. A cube that sinks through the table would
    still pass a falling test."""
    source = MujocoTwinSource(cube_height_m=1.10)

    for _ in range(120):
        source.step()

    resting_z = source.cube_position()[2]
    assert resting_z == pytest.approx(TABLE_TOP_M + CUBE_HALF_M, abs=0.02)


def test_the_cube_stays_put_once_settled():
    source = MujocoTwinSource(cube_height_m=1.10)
    for _ in range(120):
        source.step()

    settled = source.cube_position()
    for _ in range(60):
        source.step()
    after = source.cube_position()

    drift = max(abs(a - b) for a, b in zip(settled, after, strict=True))
    assert drift < 0.01, f"settled cube drifted {drift:.4f} m"


def test_is_deterministic():
    """Same model, same steps, same numbers -- so a bug seen in a recording can
    be reproduced (STRUCT_2.md 59 phase 12 determinism check)."""
    a, b = MujocoTwinSource(), MujocoTwinSource()
    for _ in range(50):
        a.step()
        b.step()

    assert a.state().robot.joint_positions == b.state().robot.joint_positions
    assert a.cube_position() == b.cube_position()


def test_timestamps_advance_by_the_control_period():
    source = MujocoTwinSource(control_hz=30.0)

    before = source.state().timestamp_ns
    source.step()
    after = source.state().timestamp_ns

    assert after - before == pytest.approx(1e9 / 30.0, abs=1e6)


def test_the_arm_moves_when_it_is_commanded():
    """Position actuators actually drive the joints; without this the robot is
    decorative and REPLAY means nothing."""
    source = MujocoTwinSource()
    start = source.state().robot.joint_positions

    source.command([0.6, -0.4, 0.8, 0.0, 0.3, 0.0])
    for _ in range(60):
        source.step()
    moved = source.state().robot.joint_positions

    assert max(abs(a - b) for a, b in zip(start, moved, strict=True)) > 0.1


def test_carries_the_scene_objects_a_client_renders():
    source = MujocoTwinSource()

    ids = {o.id for o in source.state().objects}

    assert {"cube_01", "bin_01"} <= ids


def test_reset_returns_to_the_initial_state():
    source = MujocoTwinSource(cube_height_m=1.10)
    initial = source.cube_position()
    for _ in range(60):
        source.step()

    source.reset()

    assert source.cube_position() == initial
    assert source.state().timestamp_ns == source.epoch_ns
