"""shell.json -- output of stage 5 (splat minus objects -> reusable room shell).

The shell is a first-class artifact, not an intermediate: every cousin
references it. Holes left by removed objects are recorded honestly rather than
inpainted -- the cousin composer biases substitute placement to occlude them,
and a gate checks that instead of hoping.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef
from ..geometry import AABB, Plane, SupportSurface
from ..provenance import Measured

SCHEMA_VERSION = "1.0.0"


class Hole(BaseModel):
    source_object_id: str
    label: str
    aabb_m: AABB
    n_gaussians_removed: int = 0


class ShellPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    reconstruction_ref: ArtifactRef
    segmentation_ref: ArtifactRef

    shell_ply_ref: ArtifactRef | None = None  # object gaussians stripped
    n_gaussians_kept: int = 0
    n_gaussians_removed: int = 0
    nurec_usd_ref: ArtifactRef | None = None
    matte_object: bool = True
    proxy_mesh_ref: ArtifactRef | None = None  # collision proxy for the whole shell

    floor: Plane
    walls: list[Plane] = Field(default_factory=list)
    ceiling: Plane | None = None
    room_polygon_m: list[tuple[float, float]] = Field(default_factory=list)
    room_area_m2: float = 0.0
    room_height_m: Measured[float] | None = None
    # floor + every fixed horizontal surface (table tops belong to assets, not here)
    placeable_surfaces: list[SupportSurface] = Field(default_factory=list)
    holes: list[Hole] = Field(default_factory=list)
