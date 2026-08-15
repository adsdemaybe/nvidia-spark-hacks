"""Acceptance gate for FOLLOW (STRUCT_2.md 22, 32, 64).

The client's job is to say where the human is and where the robot should ideally
be. It does not decide how the robot gets there -- navigation may reject the
target, and that is not an AR failure (STRUCT_2.md 24).
"""
from __future__ import annotations

import math

import pytest
from arxr.core.follow import follow_target
from arxr.core.schemas import FollowState, Pose
from pydantic import ValidationError

IDENTITY = (0.0, 0.0, 0.0, 1.0)
YAW_90 = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))


def test_target_trails_the_human_along_their_forward_axis():
    """STRUCT_2.md 22: target = position - forward * distance. Forward is +X in
    the canonical Z-up right-handed frame, which is what reproduces the geometry
    of the worked example in 32."""
    target = follow_target(Pose(position_m=(1.0, 2.0, 0.0), orientation_xyzw=IDENTITY), 1.0)

    assert target == pytest.approx((0.0, 2.0, 0.0), abs=1e-12)


def test_follow_distance_scales_the_offset():
    target = follow_target(Pose(position_m=(1.0, 2.0, 0.0), orientation_xyzw=IDENTITY), 1.5)

    assert target == pytest.approx((-0.5, 2.0, 0.0), abs=1e-12)


def test_target_follows_the_humans_heading():
    """Turn the human 90 degrees about Z and the target swings with them."""
    target = follow_target(Pose(position_m=(0.0, 0.0, 0.0), orientation_xyzw=YAW_90), 2.0)

    assert target == pytest.approx((0.0, -2.0, 0.0), abs=1e-12)


def test_target_is_always_the_requested_distance_away():
    pose = Pose(position_m=(3.0, -1.0, 0.5), orientation_xyzw=YAW_90)

    target = follow_target(pose, 1.5)

    separation = math.dist(target, pose.position_m)
    assert separation == pytest.approx(1.5, abs=1e-12)


def test_negative_follow_distance_is_rejected():
    """A negative distance would put the robot in front of the human, walking
    backwards into them."""
    with pytest.raises(ValueError, match="distance"):
        follow_target(Pose(position_m=(0.0, 0.0, 0.0), orientation_xyzw=IDENTITY), -1.0)


# STRUCT_2.md 32. Note: the spec's worked example carries
# desired_follow_distance_m = 1.5 while its follow_target sits 1.0 m away. The
# contract is a message envelope, so it validates shape, not that the sender did
# the arithmetic right -- follow_target() above is what owns the math.
FOLLOW_STATE_EXAMPLE = {
    "schema_version": "1.0",
    "timestamp_ns": 1700000000000000000,
    "human_pose": {"position_m": [1.0, 2.0, 0.0], "orientation_xyzw": [0, 0, 0, 1]},
    "desired_follow_distance_m": 1.5,
    "follow_target": {"position_m": [0.0, 2.0, 0.0]},
}


def test_spec_example_validates_as_follow_state():
    state = FollowState.model_validate(FOLLOW_STATE_EXAMPLE)

    assert state.desired_follow_distance_m == 1.5
    assert state.follow_target.position_m == (0.0, 2.0, 0.0)


def test_follow_state_rejects_non_positive_distance():
    payload = FOLLOW_STATE_EXAMPLE | {"desired_follow_distance_m": 0.0}

    with pytest.raises(ValidationError):
        FollowState.model_validate(payload)
