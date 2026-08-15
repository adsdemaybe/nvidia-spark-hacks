"""Acceptance gate for the frozen spatial contracts (STRUCT_2.md 60).

The example payloads in this file are transcribed verbatim from STRUCT_2.md
29-34. They are the contract. If a change here is needed, the spec changes
first -- see CLAUDE.md > Conventions.
"""
from __future__ import annotations

import pytest
from arxr.core.schemas import SpatialFrame
from pydantic import ValidationError

# STRUCT_2.md 29
SPATIAL_FRAME_EXAMPLE = {
    "schema_version": "1.0",
    "timestamp_ns": 1700000000000000000,
    "source": {"device_type": "phone", "input_type": "tracked_controller"},
    "frame": "struct_world",
    "position_m": [0.31, 0.18, 0.42],
    "orientation_xyzw": [0.02, 0.71, 0.03, 0.70],
    "gripper": 1.0,
}


def test_spec_example_validates_as_spatial_frame():
    frame = SpatialFrame.model_validate(SPATIAL_FRAME_EXAMPLE)

    assert frame.timestamp_ns == 1700000000000000000
    assert frame.frame == "struct_world"
    assert frame.position_m == (0.31, 0.18, 0.42)
    assert frame.gripper == 1.0


def test_unknown_schema_version_is_rejected():
    payload = SPATIAL_FRAME_EXAMPLE | {"schema_version": "2.0"}

    with pytest.raises(ValidationError):
        SpatialFrame.model_validate(payload)


def test_non_unit_quaternion_is_rejected():
    """A quaternion that is not unit-norm is not a rotation. Letting one through
    silently corrupts every pose downstream of it (STRUCT_2.md 62)."""
    payload = SPATIAL_FRAME_EXAMPLE | {"orientation_xyzw": [1.0, 1.0, 0.0, 0.0]}

    with pytest.raises(ValidationError, match="unit"):
        SpatialFrame.model_validate(payload)


def test_nan_position_is_rejected():
    payload = SPATIAL_FRAME_EXAMPLE | {"position_m": [0.0, float("nan"), 0.0]}

    with pytest.raises(ValidationError, match="finite"):
        SpatialFrame.model_validate(payload)


def test_orientation_is_normalized_on_ingest():
    """Downstream IK and retargeting should never have to re-normalize. The spec
    examples are printed to 2 decimals (norm 0.9977), so this is not academic."""
    frame = SpatialFrame.model_validate(SPATIAL_FRAME_EXAMPLE)

    norm = sum(v * v for v in frame.orientation_xyzw) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-12)


def test_slightly_denormalized_quaternion_is_accepted():
    """Float error off the wire is normal; only genuinely wrong values fail."""
    payload = SPATIAL_FRAME_EXAMPLE | {"orientation_xyzw": [0.0, 0.0, 0.0, 1.0 - 1e-7]}

    assert SpatialFrame.model_validate(payload).orientation_xyzw[3] == pytest.approx(1.0)
