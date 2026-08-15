"""Acceptance gate for FOLLOW session control — spec sections 22, 64.

The gate that matters: STOP immediately halts target generation. A robot
that keeps receiving targets after the human hit stop is the failure this
exists to prevent.

Ported/adapted from Andrew's arxr-core equivalent during the arvr/arxr
consolidation (see STATE.md) — same test intent, adjusted to this repo's
`ar_contracts` imports and `Target` (not `Pose`) for `follow_target`.
"""

from __future__ import annotations

import pytest
from ar_contracts import DEFAULT_FOLLOW_DISTANCE_M, FollowSession, Pose

IDENTITY = (0.0, 0.0, 0.0, 1.0)


def pose_at(x: float) -> Pose:
    return Pose(position_m=(x, 0.0, 0.0), orientation_xyzw=IDENTITY)


def test_a_started_session_emits_a_target():
    session = FollowSession()
    session.start()

    state = session.update(pose_at(5.0), timestamp_ns=1)

    assert state is not None
    assert state.follow_target.position_m[0] == pytest.approx(5.0 - DEFAULT_FOLLOW_DISTANCE_M)


def test_a_session_emits_nothing_before_it_is_started():
    session = FollowSession()

    assert session.update(pose_at(5.0), timestamp_ns=1) is None


def test_stop_immediately_halts_target_generation():
    session = FollowSession()
    session.start()
    session.update(pose_at(5.0), timestamp_ns=1)

    session.stop()

    assert session.update(pose_at(6.0), timestamp_ns=2) is None


def test_a_stopped_session_stays_stopped_until_restarted():
    """Stop is not pause. Resuming a stopped follow has to be deliberate."""
    session = FollowSession()
    session.start()
    session.stop()

    assert session.update(pose_at(6.0), timestamp_ns=2) is None

    session.start()
    assert session.update(pose_at(6.0), timestamp_ns=3) is not None


def test_pause_suspends_and_resume_continues():
    session = FollowSession()
    session.start()

    session.pause()
    assert session.update(pose_at(6.0), timestamp_ns=2) is None

    session.resume()
    assert session.update(pose_at(6.0), timestamp_ns=3) is not None


def test_follow_distance_is_configurable():
    session = FollowSession(follow_distance_m=0.5)
    session.start()

    state = session.update(pose_at(5.0), timestamp_ns=1)

    assert state.follow_target.position_m[0] == pytest.approx(4.5)
    assert state.desired_follow_distance_m == 0.5


def test_emitted_state_carries_the_human_pose_that_produced_it():
    """The consumer needs both halves to reason about tracking error."""
    session = FollowSession()
    session.start()

    state = session.update(pose_at(2.0), timestamp_ns=99)

    assert state.human_pose.position_m == (2.0, 0.0, 0.0)
    assert state.timestamp_ns == 99
