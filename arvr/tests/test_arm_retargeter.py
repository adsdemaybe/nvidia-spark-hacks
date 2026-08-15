"""Acceptance-gate tests for ArmRetargeter (Shadow Robot Spatial
Demonstration Pipeline spec section 52, Phase 6).
"""

from __future__ import annotations

import math

import pytest
from ar_contracts import HandFrame
from spatial_providers import MockHandProvider

pytest.importorskip("pinocchio")

from ar_datapipe import ArmRetargeter, IkSolver  # noqa: E402


def _hand(position_m, orientation_xyzw=(0.0, 0.0, 0.0, 1.0), thumb_index_gap=None) -> HandFrame:
    joints = {
        "wrist": {"position_m": position_m, "orientation_xyzw": orientation_xyzw},
    }
    if thumb_index_gap is not None:
        joints["thumb-tip"] = {
            "position_m": (position_m[0] - thumb_index_gap / 2, position_m[1], position_m[2]),
            "orientation_xyzw": orientation_xyzw,
        }
        joints["index-finger-tip"] = {
            "position_m": (position_m[0] + thumb_index_gap / 2, position_m[1], position_m[2]),
            "orientation_xyzw": orientation_xyzw,
        }
    return HandFrame(timestamp_ns=0, source_device="mock", hand="right", joints=joints)


def _hand_without_wrist() -> HandFrame:
    return HandFrame(
        timestamp_ns=0,
        source_device="mock",
        hand="right",
        joints={
            "thumb-tip": {"position_m": (0.0, 0.0, 0.0), "orientation_xyzw": (0, 0, 0, 1)},
        },
    )


def test_reachable_wrist_converges_ok():
    retargeter = ArmRetargeter(IkSolver())
    result = retargeter.step(_hand((0.5, 0.0, 0.6)), timestamp_ns=0)
    assert result.ik_status == "ok"
    assert all(math.isfinite(x) for x in result.q)
    assert all(math.isfinite(x) for x in result.dq)


def test_unreachable_wrist_reports_failed_not_silently_approximated():
    retargeter = ArmRetargeter(IkSolver())
    # Far outside the arm's ~0.9m reach.
    result = retargeter.step(_hand((50.0, 50.0, 50.0)), timestamp_ns=0)
    assert result.ik_status != "ok"
    assert all(math.isfinite(x) for x in result.q)


def test_gripper_tracks_pinch_aperture():
    retargeter = ArmRetargeter(IkSolver())
    closed = retargeter.step(_hand((0.5, 0.0, 0.6), thumb_index_gap=0.015), timestamp_ns=0)
    open_ = retargeter.step(_hand((0.5, 0.0, 0.6), thumb_index_gap=0.08), timestamp_ns=1)
    assert closed.gripper > open_.gripper
    assert closed.gripper == pytest.approx(1.0, abs=1e-6)
    assert open_.gripper == pytest.approx(0.0, abs=1e-6)


def test_missing_wrist_holds_last_position_and_reports_failed():
    retargeter = ArmRetargeter(IkSolver())
    first = retargeter.step(_hand((0.5, 0.0, 0.6)), timestamp_ns=0)
    held = retargeter.step(_hand_without_wrist(), timestamp_ns=1)
    assert held.ik_status == "failed"
    assert held.q == first.q
    assert held.dq == tuple(0.0 for _ in held.dq)


def test_missing_wrist_on_first_frame_does_not_crash():
    retargeter = ArmRetargeter(IkSolver())
    result = retargeter.step(_hand_without_wrist(), timestamp_ns=0)
    assert result.ik_status == "failed"
    assert all(math.isfinite(x) for x in result.q)


def test_feeding_mock_episode_keeps_dq_bounded_at_most_frames():
    """Not a strict bound at every frame (a legitimate large jump can still
    happen), but the mock episode's motion is smooth enough that dq should
    stay small almost everywhere -- mirrors pipeline.py's existing
    _velocity_ok discipline, now available per-step instead of post-hoc."""
    retargeter = ArmRetargeter(IkSolver())
    frames = list(MockHandProvider().stream())
    dq_norms = []
    for frame in frames:
        result = retargeter.step(frame, timestamp_ns=frame.timestamp_ns)
        assert all(math.isfinite(x) for x in result.q)
        assert all(math.isfinite(x) for x in result.dq)
        dq_norms.append(math.sqrt(sum(x * x for x in result.dq)))

    # First frame's dq is always zero (no prior sample); check the rest.
    over_limit = sum(1 for n in dq_norms[1:] if n > 50.0)
    assert over_limit <= 2, f"too many large dq jumps: {dq_norms}"
