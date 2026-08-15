"""Acceptance gate for the mock TwinState source (STRUCT_2.md 58, 65).

Every client mode develops against this before the Isaac bridge exists. Its one
hard requirement: what comes out is indistinguishable in shape from what Isaac
will send, so swapping the provider needs no client rewrite (STRUCT_2.md 25).
"""
from __future__ import annotations

import pytest
from arxr.core.schemas import TwinState
from arxr.core.twin_mock import MockTwinSource


def test_emits_a_valid_twin_state():
    source = MockTwinSource(scene_id="demo_room")

    state = source.at_tick(0)

    assert TwinState.model_validate(state.model_dump()) == state
    assert state.scene_id == "demo_room"


def test_is_deterministic_for_a_given_tick():
    """Two clients joining the stream at the same tick must see the same world,
    and a replayed bug must reproduce."""
    a = MockTwinSource(scene_id="demo_room").at_tick(42)
    b = MockTwinSource(scene_id="demo_room").at_tick(42)

    assert a == b


def test_the_robot_actually_moves():
    """A twin whose joints never change would pass every schema check and still
    prove nothing about the client's rendering (STRUCT_2.md 65)."""
    source = MockTwinSource(scene_id="demo_room")

    first = source.at_tick(0).robot.joint_positions
    later = source.at_tick(15).robot.joint_positions

    assert first != later


def test_timestamps_advance_with_the_configured_rate():
    source = MockTwinSource(scene_id="demo_room", hz=30.0)

    delta_ns = source.at_tick(1).timestamp_ns - source.at_tick(0).timestamp_ns

    # Integer nanoseconds, so one tick of rounding is expected and fine.
    assert delta_ns == pytest.approx(1e9 / 30.0, abs=1.0)
    assert isinstance(delta_ns, int)


def test_carries_the_scene_objects_a_client_has_to_render():
    source = MockTwinSource(scene_id="demo_room")

    state = source.at_tick(0)

    assert {o.id for o in state.objects} == {"cube_01", "bin_01"}
    assert state.task is not None
