"""SpatialFrame — spec section 29.

The universal per-sample output of every SpatialAdapter (phone, XR
controller, hand tracking, desktop mock). Every downstream robot pipeline
consumes SpatialFrame and nothing device-specific (spec section 5).
"""

from __future__ import annotations

from pydantic import model_validator

from .common import (
    SCHEMA_VERSION,
    CoordinateFrame,
    FrozenModel,
    OrientationXYZW,
    PositionM,
    SchemaVersion,
    Source,
    TimestampNs,
)


class SpatialFrame(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    timestamp_ns: TimestampNs
    source: Source
    frame: CoordinateFrame
    position_m: PositionM
    orientation_xyzw: OrientationXYZW
    # 0.0 = fully open, 1.0 = fully closed. Always present (default 0.0/open)
    # rather than nullable — matches the spec's own example (section 29) and
    # the xr-web client's SpatialFrame.gripper: number, which is non-optional.
    # Reconciled during the arvr/arxr consolidation (see STATE.md).
    gripper: float = 0.0

    @model_validator(mode="after")
    def _gripper_range(self) -> SpatialFrame:
        if not (0.0 <= self.gripper <= 1.0):
            raise ValueError("gripper must be in [0.0, 1.0] (0=open, 1=closed)")
        return self
