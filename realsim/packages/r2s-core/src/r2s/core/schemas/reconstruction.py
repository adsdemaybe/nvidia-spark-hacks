"""reconstruction.json -- output of stage 2 (frames -> posed splat + metric frame)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef
from ..geometry import Plane
from ..provenance import Measured
from .capture import CameraPrior

SCHEMA_VERSION = "1.0.0"


class SfMSummary(BaseModel):
    n_images_input: int
    n_images_registered: int
    n_points3d: int
    mean_reproj_error_px: float
    camera_model: str = "OPENCV"
    intrinsics: dict[str, CameraPrior] = Field(default_factory=dict)  # camera_id -> prior
    sparse_ref: ArtifactRef | None = None  # COLMAP model dir (tar)
    n_submodels: int = 1  # >1 means the scene fragmented

    @property
    def registration_ratio(self) -> float:
        return self.n_images_registered / max(self.n_images_input, 1)


class SplatSummary(BaseModel):
    method: Literal["3dgut", "3dgrt", "gsplat", "torch_ref", "stub"]
    config_name: str = ""
    n_gaussians: int = 0
    iterations: int = 0
    val_psnr: float = 0.0
    val_ssim: float = 0.0
    val_split: list[str] = Field(default_factory=list)  # held-out frames; must be non-empty
    ply_ref: ArtifactRef | None = None
    checkpoint_ref: ArtifactRef | None = None


class ScaleSolution(BaseModel):
    """SfM units -> metres. The single most load-bearing number in the pipeline."""

    scale_factor: Measured[float]
    method: Literal["lidar_sim3", "known_object", "aruco", "assumed"] = "assumed"
    inlier_ratio: float | None = None
    rmse_m: float | None = None


class NuRecExport(BaseModel):
    usd_ref: ArtifactRef
    prim_path: str = "/World/Scan/NuRec"
    up_axis: Literal["Y", "Z"] = "Z"
    meters_per_unit: float = 1.0
    exporter_flag_used: str = ""  # which of export_usd/export_usdz actually worked
    loaded_by_usd_core: bool = False
    n_prims: int = 0


class ReconstructionPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    capture_ref: ArtifactRef
    sfm: SfMSummary
    splat: SplatSummary
    scale: ScaleSolution
    world_up: tuple[float, float, float] = (0.0, 0.0, 1.0)
    floor_plane: Plane | None = None
    # 4x4 row-major: splat/SfM frame -> gravity-aligned metric world
    metric_transform: list[float] = Field(default_factory=lambda: _identity16())
    nurec: NuRecExport | None = None
    room_extent_m: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _identity16() -> list[float]:
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
