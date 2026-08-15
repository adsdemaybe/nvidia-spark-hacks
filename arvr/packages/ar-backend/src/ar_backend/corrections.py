"""Corrections API — spec section 40.

    POST /xr/corrections

Captures a `CorrectionEvent` — the point is the before/after pair (spec
section 27); this endpoint's only job is to validate and store it. AR/XR
owns capture; the training system owns learning from it, so this
deliberately does nothing more than append to an in-memory list.
"""

from __future__ import annotations

from ar_contracts import CorrectionEvent
from fastapi import APIRouter

from .store import CorrectionStore


def build_router(store: CorrectionStore) -> APIRouter:
    router = APIRouter(prefix="/xr/corrections", tags=["corrections"])

    @router.post("", response_model=CorrectionEvent)
    def create_correction(event: CorrectionEvent) -> CorrectionEvent:
        store.add(event)
        return event

    @router.get("", response_model=list[CorrectionEvent])
    def list_corrections() -> list[CorrectionEvent]:
        return store.all()

    return router
