"""asset_bundle/bundle.json + per-asset asset.json -- output of stage 4.

An asset is self-contained on disk:

    assets/<asset_id>/
      asset.json          # AssetPayload
      visual/mesh.glb     # or gaussians.ply for SCANNED assets
      collision/hull_000.obj ...
      thumbs/front.png

so it can later be published to a shared library without path rewriting.
GENERATED and PROCEDURAL assets flow through the exact same assetize stage as
SCANNED ones -- one pipeline, three producers.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef, DegradationLevel
from ..geometry import Pose
from ..provenance import AssetProvenance, Measured, Proposer

SCHEMA_VERSION = "1.0.0"


class PhysicalProperties(BaseModel):
    """Every field is Measured -- there is no way to write a bare float here."""

    mass: Measured[float]  # kg
    density: Measured[float]  # kg/m^3, the prior that produced mass
    com_local: Measured[tuple[float, float, float]]
    inertia_diag: Measured[tuple[float, float, float]]  # principal moments, kg*m^2
    static_friction: Measured[float]
    dynamic_friction: Measured[float]
    restitution: Measured[float]
    material_guess: str = "unknown"


class CollisionSummary(BaseModel):
    method: Literal["coacd", "vhacd", "convex_hull", "obb", "stub"]
    n_hulls: int = 0
    total_vertices: int = 0
    volume_ratio: float = 0.0  # hull volume / mesh volume; 1.0 is perfect
    watertight: bool = False
    max_hull_vertices: int = 0


class SupportFace(BaseModel):
    """A horizontal face of THIS asset that other objects can rest on.
    Polygon is in the asset's canonical frame, already eroded by the edge margin."""

    face_id: str
    height_m: float  # above asset origin (origin = AABB center-bottom)
    polygon: list[tuple[float, float]]
    area_m2: float


class AssetPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    asset_id: str
    label: str
    category: str
    provenance: AssetProvenance
    proposer: Proposer  # who proposed this asset exists (segmentation / LLM descriptor / rule)
    descriptor: str = ""  # for GENERATED: the text that produced it
    source_object_id: str | None = None  # which scanned object it derives from / stands in for
    generation_cache_key: str | None = None  # sha256(prompt+model+seed) for GENERATED

    # geometry, canonical frame: z-up, origin at AABB center-bottom, front = +x
    bbox_m: tuple[float, float, float]
    visual_mesh_ref: ArtifactRef | None = None  # glb/obj
    gaussians_ref: ArtifactRef | None = None  # SCANNED only
    collision_refs: list[ArtifactRef] = Field(default_factory=list)
    collision: CollisionSummary
    stable_poses: list[Pose] = Field(default_factory=list)
    stable_pose_probs: list[float] = Field(default_factory=list)
    support_faces: list[SupportFace] = Field(default_factory=list)

    physics: PhysicalProperties

    # index-time filters (computed once, consumed by retrieval/generation ranking)
    graspable_width_m: Measured[float] | None = None  # min antipodal width, None = ungraspable
    fits_so101_gripper: bool = False
    thumbnail_ref: ArtifactRef | None = None
    degradation: DegradationLevel = DegradationLevel.FULL


class AssetBundlePayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    segmentation_ref: ArtifactRef | None = None  # None for a pure library bundle
    asset_ids: list[str] = Field(default_factory=list)
    assets: dict[str, AssetPayload] = Field(default_factory=dict)  # asset_id -> payload
    bundle_dir_ref: ArtifactRef | None = None  # tar of the on-disk bundle
    dropped: dict[str, str] = Field(default_factory=dict)  # asset_id -> reason (visible, not silent)
