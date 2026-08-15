"""VerificationResult — spec section 13A lists this contract by name but the
spec gives no literal JSON example for it. This shape is derived from the
concrete requirements elsewhere in the spec and should be treated as
provisional until reviewed alongside the retarget/verify pipeline (Phase 5-6):

  - fields mirror the `GET /xr/episodes/{id}` example (section 36):
    status, tracking_error_m, task_success, dataset_id.
  - `checks` mirrors the acceptance-gate checklist shown in the demo script
    (section 75): IK, joint limits, replay, task predicate.
  - rejected demonstrations must carry a machine-readable reason (spec
    section 63: "Rejected demos must return a measurable reason"; rule
    85.10: "preserve rejected episode reasons") — enforced structurally
    below, not left to convention.
  - rule 85.9 ("never mark verification success from an LLM judgment") is
    enforced by construction: every field here is a typed bool/float, there
    is no free-text "verdict" field an LLM could fill in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion

VerificationStatus = Literal["accepted", "rejected"]


class VerificationChecks(FrozenModel):
    ik: bool
    joint_limits: bool
    replay: bool
    task_predicate: bool


class VerificationResult(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    episode_id: str
    status: VerificationStatus
    checks: VerificationChecks
    tracking_error_m: float | None = None
    task_success: bool | None = None
    dataset_id: str | None = None
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def _status_invariants(self) -> VerificationResult:
        if self.status == "rejected" and not self.rejection_reason:
            raise ValueError(
                "rejected VerificationResult must include rejection_reason "
                "(spec section 63 / rule 85.10)"
            )
        if self.status == "accepted" and not self.dataset_id:
            raise ValueError("accepted VerificationResult must include dataset_id")
        return self
