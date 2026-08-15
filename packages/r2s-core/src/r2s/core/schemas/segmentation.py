"""segmentation.json -- output of stage 3 (splat -> labeled 3D object clusters)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..artifacts import ArtifactRef
from ..geometry import AABB, OBB
from ..provenance import Measured, Proposer

SCHEMA_VERSION = "1.0.0"


class PromptSpec(BaseModel):
    """The ONE place an LLM is allowed upstream of envgen: proposing the prompt
    vocabulary. The proposer tag records who. The masks and every verdict about
    them are deterministic."""

    text: str  # "mug . chair . laptop"
    proposer: Proposer
    box_threshold: float = 0.35
    text_threshold: float = 0.25


class Segment3D(BaseModel):
    object_id: str  # "obj_003"
    label: str  # normalized category ("mug")
    prompt_term: str = ""
    proposer: Proposer
    gaussian_indices_ref: ArtifactRef | None = None  # int32 .npy into the splat PLY ordering
    n_gaussians: int = 0
    n_views_supporting: int = 0
    n_views_total: int = 0
    vote_ratio: float = 0.0  # mean per-gaussian in-mask fraction
    aabb_m: AABB
    obb_m: OBB | None = None
    centroid_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    volume_m3: Measured[float] | None = None
    is_movable_guess: bool = True  # proposed, NOT a verdict
    # crop of the best view, for CLIP ranking + human review
    thumbnail_ref: ArtifactRef | None = None


class SegmentationPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    reconstruction_ref: ArtifactRef
    prompts: list[PromptSpec] = Field(default_factory=list)
    detector: str = ""  # "IDEA-Research/grounding-dino-base" | "google/owlv2-base-patch16"
    segmenter: str = ""  # "facebook/sam2.1-hiera-large"
    lift_method: Literal[
        "projection_depthtest_majority", "contribution_weighted", "fixture"
    ] = "fixture"
    objects: list[Segment3D] = Field(default_factory=list)
    n_gaussians_assigned: int = 0
    n_gaussians_total: int = 0
    assignment_conflicts: int = 0
