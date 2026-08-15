"""scene.json -- one digital cousin (or the twin). Stage 6 emits N of these."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef
from ..geometry import Pose
from ..provenance import AssetProvenance, Proposer

SCHEMA_VERSION = "1.0.0"


class Placement(BaseModel):
    instance_id: str  # "mug_0" -- unique within the scene
    asset_id: str
    asset_provenance: AssetProvenance
    source_object_id: str | None = None  # scanned object this stands in for
    category: str
    pose: Pose  # sampled pose, metric world
    settled_pose: Pose | None = None  # after the drop test; the one baked into exports
    scale: float = 1.0
    support_surface_id: str = ""
    settle_translation_m: float | None = None
    settle_rotation_deg: float | None = None
    role: Literal["target", "receptacle", "distractor", "fixture"] = "distractor"
    prim_path: str = ""  # "/World/obj_003" -- tasks bind by this, never by label


class VariationRecord(BaseModel):
    axis: Literal["identity", "layout", "placement", "task", "lighting", "clutter"]
    description: str
    proposer: Proposer
    params: dict[str, Any] = Field(default_factory=dict)


class RobotSpec(BaseModel):
    name: str = "so101"
    base_pose: Pose = Pose()
    # SO-101 workspace, measured from the URDF once and confirmed there.
    reach_min_m: float = 0.08
    reach_max_m: float = 0.35
    height_band_m: tuple[float, float] = (-0.05, 0.40)  # relative to base
    gripper_max_width_m: float = 0.08
    payload_kg: float = 0.25


class SettleReport(BaseModel):
    ran: bool = False
    sim: Literal["mujoco", "isaac", "none"] = "none"
    steps: int = 0
    dt: float = 0.0
    max_translation_m: float = 0.0
    max_rotation_deg: float = 0.0
    max_terminal_speed: float = 0.0
    max_penetration_m: float = 0.0
    exploded: bool = False


class ScenePayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    scene_id: str  # "c0" .. "c7"
    kind: Literal["TWIN", "COUSIN"]
    cousin_index: int
    split: Literal["train", "heldout"]
    seed: int
    shell_ref: ArtifactRef
    render_mode: Literal["pure_splat", "hybrid_splat_mesh"]
    robot: RobotSpec
    placements: list[Placement] = Field(default_factory=list)
    variations: list[VariationRecord] = Field(default_factory=list)
    usd_ref: ArtifactRef | None = None  # scene.usda
    mjcf_ref: ArtifactRef | None = None  # scene.xml -- the validator's view
    thumbnail_ref: ArtifactRef | None = None
    physics_settle: SettleReport = Field(default_factory=SettleReport)
    diversity_vs_siblings: dict[str, float] = Field(default_factory=dict)

    def asset_ids(self) -> set[str]:
        return {p.asset_id for p in self.placements if p.role != "fixture"}
