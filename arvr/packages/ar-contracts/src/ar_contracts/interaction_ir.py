"""InteractionIR — Shadow Robot Spatial Demonstration Pipeline spec section 22.

Robot-independent physical intent, derived from a HumanEpisode plus the
InteractableAsset it was recorded against. Not mandatory for recording (a
HumanEpisode is complete and storable without one) — this is a derived
representation, built by `ar_datapipe.interaction_ir.derive_interaction_ir()`,
not captured live.
"""

from __future__ import annotations

from typing import Literal

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion

InteractionPhaseType = Literal[
    "approach", "contact", "press", "pull", "retract", "grasp", "release",
]


class InteractionPhase(FrozenModel):
    type: InteractionPhaseType
    target_position_m: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    distance_m: float | None = None


class InteractionIR(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    task_id: str
    asset_id: str
    reference_frame: str
    phases: tuple[InteractionPhase, ...]
