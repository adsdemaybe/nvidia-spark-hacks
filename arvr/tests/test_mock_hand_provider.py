"""Acceptance-gate tests for MockHandProvider (Shadow Robot Spatial
Demonstration Pipeline spec section 52, Phase 4).
"""

from __future__ import annotations

from ar_contracts import HAND_JOINT_NAMES
from spatial_providers import MockHandProvider


def test_mock_hand_provider_streams_monotonic_timestamps():
    frames = list(MockHandProvider().stream())
    assert len(frames) > 0
    timestamps = [f.timestamp_ns for f in frames]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)  # strictly increasing


def test_mock_hand_provider_full_schema_present():
    frames = list(MockHandProvider().stream())
    for frame in frames:
        assert set(frame.joints) == set(HAND_JOINT_NAMES)
        assert frame.hand == "right"
        assert frame.source_device == "mock"


def test_mock_hand_provider_record_playback_is_exact():
    first_run = list(MockHandProvider().stream())
    second_run = list(MockHandProvider().stream())
    assert first_run == second_run


def test_mock_hand_provider_pinch_closes_then_reopens():
    """Sanity check the "contact" window actually models a pinch, not just
    schema-valid noise -- thumb/index tips should come close together
    partway through and separate again by the end."""
    frames = list(MockHandProvider().stream())

    def aperture(i: int) -> float:
        thumb = frames[i].joints["thumb-tip"].position_m
        index = frames[i].joints["index-finger-tip"].position_m
        return sum((a - b) ** 2 for a, b in zip(thumb, index, strict=True)) ** 0.5

    mid = len(frames) // 2
    assert aperture(mid) < aperture(0)
    assert aperture(mid) < aperture(-1)
