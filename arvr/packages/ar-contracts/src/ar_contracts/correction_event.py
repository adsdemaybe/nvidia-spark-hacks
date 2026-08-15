"""CorrectionEvent — spec section 33.

Captures a human's spatial edit to a proposed robot target (CORRECT mode,
spec section 27/46). AR/XR owns capture of the before/after pair only; it
does not own learning from it (spec section 27, closing line).
"""

from __future__ import annotations

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion, Target, TimestampNs

# Free-text, not a closed enum: the spec's literal example (section 33) is
# just a string ("collision_avoidance"), and a closed Literal would reject
# any reason a client sends that isn't in a hardcoded list — including the
# xr-web client's, which types this as `reason?: string`. Was a Literal enum
# before the arvr/arxr consolidation (see STATE.md); loosened during it.
CorrectionReason = str


class CorrectionEvent(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    task_id: str
    timestamp_ns: TimestampNs
    original_target: Target
    corrected_target: Target
    reason: CorrectionReason | None = None
