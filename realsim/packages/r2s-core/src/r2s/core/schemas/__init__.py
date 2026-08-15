"""Artifact payload schemas. One module per artifact type, each with its own
SCHEMA_VERSION -- bump the version whose contract changed, not all of them."""
from .asset import AssetBundlePayload, AssetPayload, CollisionSummary, PhysicalProperties, SupportFace
from .capture import CameraPrior, CapturePayload, FrameSet, LidarScan
from .reconstruction import (
    NuRecExport,
    ReconstructionPayload,
    ScaleSolution,
    SfMSummary,
    SplatSummary,
)
from .scene import Placement, RobotSpec, ScenePayload, SettleReport, VariationRecord
from .segmentation import PromptSpec, Segment3D, SegmentationPayload
from .shell import Hole, ShellPayload
from .task import (
    LOGIC_OPS,
    MAX_DEPTH,
    MAX_LEAVES,
    PREDICATE_OPS,
    FeasibilityReport,
    Predicate,
    TaskPayload,
    Witness,
)

ARTIFACT_TYPES = {
    "capture": CapturePayload,
    "reconstruction": ReconstructionPayload,
    "segmentation": SegmentationPayload,
    "asset_bundle": AssetBundlePayload,
    "shell": ShellPayload,
    "scene": ScenePayload,
    "task": TaskPayload,
}

__all__ = [
    "ARTIFACT_TYPES",
    "LOGIC_OPS",
    "MAX_DEPTH",
    "MAX_LEAVES",
    "PREDICATE_OPS",
    "AssetBundlePayload",
    "AssetPayload",
    "CameraPrior",
    "CapturePayload",
    "CollisionSummary",
    "FeasibilityReport",
    "FrameSet",
    "Hole",
    "LidarScan",
    "NuRecExport",
    "PhysicalProperties",
    "Placement",
    "Predicate",
    "PromptSpec",
    "ReconstructionPayload",
    "RobotSpec",
    "ScaleSolution",
    "ScenePayload",
    "Segment3D",
    "SegmentationPayload",
    "SettleReport",
    "SfMSummary",
    "ShellPayload",
    "SplatSummary",
    "SupportFace",
    "TaskPayload",
    "VariationRecord",
    "Witness",
]
