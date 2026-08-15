"""Computed mass properties for a built link — never authored in the IR.

Non-negotiable #2 (§11): "Never freehand a computed number — inertia, CoM,
torque budgets are computed, not guessed." This module is the only place
mass/CoM/volume are allowed to originate; everything downstream (criteria,
tier 0/1 physics) reads from a MassProperties instance, never from an
agent-authored float.
"""

from __future__ import annotations

from engine.ir import Vec3

from pydantic import BaseModel, model_validator


class MassProperties(BaseModel):
    mass: float  # kg
    volume: float  # m^3
    com: Vec3  # meters, in the link-local frame
    bbox_min: Vec3  # meters, in the link-local frame
    bbox_max: Vec3  # meters, in the link-local frame

    @model_validator(mode="after")
    def _physically_sane(self) -> "MassProperties":
        if self.mass <= 0:
            raise ValueError(f"computed mass must be positive, got {self.mass}")
        if self.volume <= 0:
            raise ValueError(f"computed volume must be positive, got {self.volume}")
        return self

    @property
    def bbox_size(self) -> Vec3:
        return Vec3(
            x=self.bbox_max.x - self.bbox_min.x,
            y=self.bbox_max.y - self.bbox_min.y,
            z=self.bbox_max.z - self.bbox_min.z,
        )
