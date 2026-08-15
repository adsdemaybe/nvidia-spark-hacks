"""HandFrame — Shadow Robot Spatial Demonstration Pipeline spec section 17.

The canonical per-frame hand-tracking record. The human's hand IS the input
device in this pipeline (spec section 12), so this is where physical-world
motion enters the system — everything downstream (shadow hand, retargeting,
recording) is built on this shape.

Mirrors `packages/xr-web/src/hands.ts`'s `HAND_JOINTS` + `PALM_JOINT` list
exactly (26 possible joint names) — kept in sync manually, same drift-risk
already accepted between `contracts.ts` and this package (see
`docs/CONTRACTS.md`). Positions here are always `struct_world`
(right-handed, Z-up, meters); `hands.ts::readHand()` returns raw WebXR/Y-up
poses and must be converted at the recording boundary before reaching this
model — never inside the shadow-hand renderer, which stays in WebXR space
on purpose (see `shadowHand.ts`).

An untracked joint is simply absent from `joints`, never a frame of zeros —
zeros would retarget into a real robot pose at the origin (spec section 17:
"the canonical representation should allow missing joints").
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from .common import (
    SCHEMA_VERSION,
    CoordinateFrame,
    FrozenModel,
    OrientationXYZW,
    PositionM,
    SchemaVersion,
    TimestampNs,
)

HandSide = Literal["left", "right"]
HandSourceDevice = Literal["openxr", "phone", "mock", "webcam"]

# Matches xr-web/src/hands.ts's HAND_JOINTS + PALM_JOINT exactly.
HAND_JOINT_NAMES: tuple[str, ...] = (
    "wrist",
    "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
    "index-finger-metacarpal", "index-finger-phalanx-proximal",
    "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
    "middle-finger-metacarpal", "middle-finger-phalanx-proximal",
    "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
    "ring-finger-metacarpal", "ring-finger-phalanx-proximal",
    "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
    "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal",
    "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip",
    "palm",
)


class HandJoint(FrozenModel):
    position_m: PositionM
    orientation_xyzw: OrientationXYZW
    confidence: float = 1.0
    tracked: bool = True

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        return v


class HandFrame(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    timestamp_ns: TimestampNs
    source_device: HandSourceDevice
    hand: HandSide
    frame: CoordinateFrame = "struct_world"
    joints: dict[str, HandJoint] = {}

    @field_validator("joints")
    @classmethod
    def _known_joint_names(cls, v: dict[str, HandJoint]) -> dict[str, HandJoint]:
        unknown = set(v) - set(HAND_JOINT_NAMES)
        if unknown:
            raise ValueError(f"unknown hand joint name(s): {sorted(unknown)}")
        return v
