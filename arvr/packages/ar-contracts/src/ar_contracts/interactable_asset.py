"""InteractableAsset — Shadow Robot Spatial Demonstration Pipeline spec
section 15.

A raw GLB is visual data only. An InteractableAsset adds the physical and
semantic interaction metadata a task actually needs — what part, what kind
of interaction, along which axis, how far. Different interaction kinds use
different optional fields (a button's `travel_m` vs. a drawer's
`joint_type`/`limit_m`) rather than one contract trying to cover every case
with required fields, matching spec section 15's own button/drawer examples.
"""

from __future__ import annotations

from typing import Literal

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion

InteractionKind = Literal["press", "pull", "grasp", "push", "rotate"]
AssetJointType = Literal["prismatic", "revolute"]


class AssetPart(FrozenModel):
    interaction: InteractionKind
    local_origin_m: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    travel_m: float | None = None
    joint_type: AssetJointType | None = None
    limit_m: tuple[float, float] | None = None


class InteractableAsset(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    asset_id: str
    parts: dict[str, AssetPart]
