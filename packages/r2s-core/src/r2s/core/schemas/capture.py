"""capture.json -- the output of stage 1 (video -> curated frames)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef
from ..provenance import Measured

SCHEMA_VERSION = "1.0.0"


class CameraPrior(BaseModel):
    """Best-guess intrinsics before SfM refines them. OPENCV convention."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    model: str = "OPENCV"
    source: Literal["exif", "assumed", "colmap"] = "assumed"


class FrameSet(BaseModel):
    dir_ref: ArtifactRef  # deterministic tar of PNGs
    count: int
    width: int
    height: int
    fps_sampled: float
    naming: str = "{:05d}.png"
    sharpness_p10: float
    sharpness_median: float
    dropped_blurry: int = 0
    dropped_subsample: int = 0
    exposure_cv: float = 0.0  # coefficient of variation of frame means; AE pumping detector


class LidarScan(BaseModel):
    """Optional metric mesh (Polycam/Scaniverse). This is the scale anchor --
    without it every downstream mass/inertia/reach number is fiction and the
    reconstruction is stamped DEGRADED."""

    mesh_ref: ArtifactRef
    format: Literal["obj", "glb", "ply", "usdz"]
    source_app: Literal["polycam", "scaniverse", "other"] = "other"
    declared_metric: bool = True
    n_vertices: int = 0
    aabb_m: tuple[float, float, float] = (0.0, 0.0, 0.0)


class CapturePayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    video_ref: ArtifactRef | None = None  # None when the input was a frame dir
    video_duration_s: float = 0.0
    video_fps: float = 0.0
    frames: FrameSet
    intrinsics_prior: CameraPrior | None = None
    lidar: LidarScan | None = None
    orbit_coverage_deg: Measured[float] | None = None
    notes: list[str] = Field(default_factory=list)
