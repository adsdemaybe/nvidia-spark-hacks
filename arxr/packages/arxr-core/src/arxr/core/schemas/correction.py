"""CorrectionEvent -- a human fixing robot intent in space, without code.

The pair is the point. An original target alone is just a plan; a corrected
target alone is just a waypoint. Together they are supervision (STRUCT_2.md 46).
AR/XR owns capturing this; the training system owns learning from it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .pose import Pose

SCHEMA_VERSION = "1.0"


class CorrectionEvent(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    task_id: str
    timestamp_ns: int
    original_target: Pose
    corrected_target: Pose
    reason: str | None = None
