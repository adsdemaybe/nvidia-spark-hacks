"""Corrections API — spec section 40.

    POST /xr/corrections
    GET  /xr/corrections

Captures a `CorrectionEvent` (spec section 27's before/after pair) and
also verifies it — spec section 70 DoD item 5: "correction can be
replayed or verified". Verification here means: is the corrected target
actually reachable by the placeholder arm? Reusing `ar_datapipe.IkSolver`
rather than inventing a second IK path for this one check.

`CorrectionEvent` itself is frozen and unchanged (rule 85.15) — the
verification result is a separate response wrapper, not a new field
bolted onto the contract.
"""

from __future__ import annotations

from ar_contracts import CorrectionEvent
from fastapi import APIRouter
from pydantic import BaseModel

from .store import CorrectionStore


class CorrectionVerification(BaseModel):
    checked: bool
    reachable: bool | None = None
    within_joint_limits: bool | None = None
    pose_error: float | None = None
    reason: str | None = None


class CorrectionResponse(BaseModel):
    event: CorrectionEvent
    verification: CorrectionVerification


def _verify_corrected_target(event: CorrectionEvent) -> CorrectionVerification:
    try:
        from ar_datapipe import IkSolver
    except ImportError as exc:
        # Same Linux-only story as ar_datapipe elsewhere (see its README) —
        # /xr/corrections still accepts and stores the event on Windows,
        # it just can't run the reachability check there.
        return CorrectionVerification(checked=False, reason=f"IK unavailable: {exc}")

    target = event.corrected_target
    solver = IkSolver()
    result = solver.solve(target.position_m, target.orientation_xyzw)
    reason = None
    if not result.converged:
        reason = f"corrected target not reachable (pose error {result.final_error_norm:.4f})"
    elif not result.within_limits:
        reason = "corrected target reachable but outside joint limits"

    return CorrectionVerification(
        checked=True,
        reachable=result.converged,
        within_joint_limits=result.within_limits,
        pose_error=result.final_error_norm,
        reason=reason,
    )


def build_router(store: CorrectionStore) -> APIRouter:
    router = APIRouter(prefix="/xr/corrections", tags=["corrections"])

    @router.post("", response_model=CorrectionResponse)
    def create_correction(event: CorrectionEvent) -> CorrectionResponse:
        store.add(event)
        return CorrectionResponse(event=event, verification=_verify_corrected_target(event))

    @router.get("", response_model=list[CorrectionEvent])
    def list_corrections() -> list[CorrectionEvent]:
        return store.all()

    return router
