"""Small geometry value types shared across artifacts.

Metres and quaternions everywhere. `Pose` uses wxyz quaternion order to match
MuJoCo and USD authoring conventions; converting at the boundary once is much
safer than carrying two conventions through the pipeline.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # w, x, y, z


class Pose(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: Vec3 = (0.0, 0.0, 0.0)
    quaternion: Quat = (1.0, 0.0, 0.0, 0.0)

    @field_validator("quaternion")
    @classmethod
    def _normalized(cls, q: Quat) -> Quat:
        n = math.sqrt(sum(c * c for c in q))
        if n < 1e-9:
            raise ValueError("degenerate quaternion")
        return tuple(c / n for c in q)  # type: ignore[return-value]


class AABB(BaseModel):
    model_config = ConfigDict(frozen=True)

    lo: Vec3
    hi: Vec3

    @property
    def extents(self) -> Vec3:
        return tuple(h - lo for h, lo in zip(self.hi, self.lo, strict=True))  # type: ignore

    @property
    def center(self) -> Vec3:
        return tuple((h + lo) / 2 for h, lo in zip(self.hi, self.lo, strict=True))  # type: ignore

    @property
    def volume(self) -> float:
        ex, ey, ez = self.extents
        return max(ex, 0.0) * max(ey, 0.0) * max(ez, 0.0)


class OBB(BaseModel):
    model_config = ConfigDict(frozen=True)

    pose: Pose
    extents: Vec3


class Plane(BaseModel):
    """Points p satisfying dot(normal, p) == offset."""

    model_config = ConfigDict(frozen=True)

    normal: Vec3
    offset: float
    inlier_count: int = 0
    inlier_ratio: float = 0.0
    rmse_m: float = 0.0


class SupportSurface(BaseModel):
    """A horizontal face objects can rest on.

    `polygon` is already eroded by the edge margin -- see envgen.geometry. That
    erosion is what stops the sampler placing a mug 3mm from a table edge and
    then burning retries on tip-overs the geometry should have prevented.
    """

    surface_id: str
    height_m: float
    polygon: list[tuple[float, float]] = Field(default_factory=list)
    area_m2: float = 0.0
    normal: Vec3 = (0.0, 0.0, 1.0)
    owner_instance_id: str | None = None  # None => the room shell (floor)
