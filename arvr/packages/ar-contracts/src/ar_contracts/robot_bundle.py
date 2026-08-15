"""RobotBundle — Shadow Robot Spatial Demonstration Pipeline spec sections 8-10.

The one contract every downstream stage (retargeter, shadow robot, simulator)
consumes, regardless of whether the robot came from `FixtureRobotProvider` or
a future `GeneratedRobotProvider` (spec section 8: "no downstream code may
branch on fixture vs. generated"). Both providers must return the identical
shape here.

Deliberate split, mirrored in `spatial_providers.RobotProvider`:

- `RobotManifest`/`RobotIR`/`RobotCapabilityProfile` are frozen pydantic —
  the wire-shaped, hashable, serializable metadata.
- `RobotBundle` itself is a plain `@dataclass(frozen=True)`, not pydantic —
  it carries filesystem `Path`s (`urdf_path`, `visual_glb_path`, `usd_path`)
  alongside the pydantic values above. A `Path` is an in-process file handle,
  not wire data; giving it its own dataclass rather than a pydantic model
  keeps that distinction explicit instead of pretending a local filesystem
  path is something a client could ever receive over the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import field_validator

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion

JointType = Literal["revolute", "prismatic", "fixed", "continuous"]
RobotSource = Literal["fixture", "generated"]
EndEffectorKind = Literal["parallel_gripper", "none", "dexterous_hand"]


class RobotJoint(FrozenModel):
    name: str
    type: JointType
    parent_link: str
    child_link: str
    origin_position_m: tuple[float, float, float]
    origin_orientation_xyzw: tuple[float, float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    lower_limit: float | None = None
    upper_limit: float | None = None
    velocity_limit: float | None = None


class RobotIR(FrozenModel):
    """Minimum articulation data (spec section 10) — a pretty GLB is not
    enough; if a provider has no articulation for a joint, it must be
    represented as missing/NOT_TRAINABLE upstream, never invented here."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    robot_id: str
    base_link: str
    end_effector_frame: str
    links: tuple[str, ...]
    joints: tuple[RobotJoint, ...]


class RobotCapabilityProfile(FrozenModel):
    arm_dof: int
    end_effector: EndEffectorKind
    finger_count: int = 0
    supports_wrist_pose: bool = True
    supports_full_hand_retarget: bool = False
    workspace_radius_m: float

    @field_validator("arm_dof", "finger_count")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be non-negative")
        return v


class RobotManifest(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    robot_id: str
    source: RobotSource
    robot_ir: str
    urdf: str
    visual_glb: str
    usd: str | None = None
    base_link: str
    end_effectors: tuple[str, ...]


@dataclass(frozen=True)
class RobotBundle:
    manifest: RobotManifest
    robot_ir: RobotIR
    capability_profile: RobotCapabilityProfile
    urdf_path: Path
    visual_glb_path: Path
    usd_path: Path | None = None
