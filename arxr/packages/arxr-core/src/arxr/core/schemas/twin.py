"""TwinState -- what the simulation currently believes is true.

This is the Feat 5 payload. The phone does not simulate the robot; it renders
this (STRUCT_2.md 25, 52). The provider behind it is swappable by design --
MockTwinStateProvider during development, the Isaac bridge on the Spark for the
real thing -- and the client must not be able to tell the difference.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .pose import Pose, require_finite

SCHEMA_VERSION = "1.0"

# "running" is what STRUCT_2.md 31 puts on the wire; the rest are the
# human-facing states listed in 26.
TaskStatus = Literal[
    "idle",
    "running",
    "approaching",
    "grasping",
    "moving",
    "placing",
    "success",
    "failed",
]


class RobotState(BaseModel):
    id: str
    joint_positions: tuple[float, ...]

    @field_validator("joint_positions")
    @classmethod
    def _joints_finite(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        return require_finite(v, "joint_positions")


class ObjectState(Pose):
    id: str


class TaskState(BaseModel):
    id: str
    status: TaskStatus = "idle"


class TwinState(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    timestamp_ns: int
    scene_id: str
    robot: RobotState
    objects: list[ObjectState] = Field(default_factory=list)
    task: TaskState | None = None
