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

# ADDITIVE CONTRACT CHANGE (needs team sign-off, see arvr/STATE.md Round 10).
#
# The original seven are unchanged. "lift", "transport" and "place" are the
# phases a pick-and-place demonstration actually has, and the button task
# never needed them because pressing a button involves no carrying. Folding
# a transport into "approach" would make the object-relative trajectory --
# the thing that makes a demonstration reusable under a different scene
# layout -- unrecoverable from the IR.
InteractionPhaseType = Literal[
    "approach", "contact", "press", "pull", "retract", "grasp", "release",
    "lift", "transport", "place",
]


class InteractionPhase(FrozenModel):
    type: InteractionPhaseType
    target_position_m: tuple[float, float, float] | None = None
    axis: tuple[float, float, float] | None = None
    distance_m: float | None = None
    # When this phase began, so the IR can be lined up against the raw
    # HumanEpisode it was derived from. Optional: the button task's IR is a
    # plan (approach here, press this far), not a timeline.
    timestamp_ns: int | None = None


class InteractionIR(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    task_id: str
    asset_id: str
    reference_frame: str
    phases: tuple[InteractionPhase, ...]
