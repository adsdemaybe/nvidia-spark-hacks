"""CorrectionEvent — spec section 33.

Captures a human's spatial edit to a proposed robot target (CORRECT mode,
spec section 27/46). AR/XR owns capture of the before/after pair only; it
does not own learning from it (spec section 27, closing line).
"""

from __future__ import annotations

from typing import Literal

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion, Target, TimestampNs

CorrectionReason = Literal[
    "collision_avoidance",
    "unreachable",
    "task_alignment",
    "human_preference",
    "other",
]


class CorrectionEvent(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    task_id: str
    timestamp_ns: TimestampNs
    original_target: Target
    corrected_target: Target
    reason: CorrectionReason | None = None
