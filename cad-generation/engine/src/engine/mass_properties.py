"""Computed mass properties for a built link — never authored in the IR.

Non-negotiable #2 (§11): "Never freehand a computed number — inertia, CoM,
torque budgets are computed, not guessed." This module is the only place
mass/CoM/volume are allowed to originate; everything downstream (criteria,
tier 0/1 physics) reads from a MassProperties instance, never from an
agent-authored float.
"""

from __future__ import annotations

import numpy as np

from engine.ir import Vec3

from pydantic import BaseModel, model_validator


class InertiaTensor(BaseModel):
    """Rotational inertia in kg*m^2, about the link's **centre of mass**, in
    link-local axes. Six independent components because the tensor is symmetric
    — the same six URDF's `<inertia>` element and MJCF's `fullinertia` want.

    About the CoM, not the link origin. Both URDF and MJCF read it that way
    (URDF pairs it with `<origin>` at the CoM), and OpenCascade already returns
    it that way — see `geometry.registry._inertia_from_shape` for why that is
    worth stating twice.
    """

    ixx: float
    iyy: float
    izz: float
    ixy: float = 0.0
    ixz: float = 0.0
    iyz: float = 0.0

    def as_matrix(self) -> np.ndarray:
        return np.array(
            [
                [self.ixx, self.ixy, self.ixz],
                [self.ixy, self.iyy, self.iyz],
                [self.ixz, self.iyz, self.izz],
            ],
            dtype=float,
        )

    def principal_moments(self) -> np.ndarray:
        """The three eigenvalues, ascending. A physical tensor has all three
        positive and obeys the triangle inequality A + B >= C; `inertia_valid`
        is the criterion that checks it, because a tensor that fails is a bug
        in the mass model, not a fact about the design.
        """
        return np.linalg.eigvalsh(self.as_matrix())


class MassProperties(BaseModel):
    mass: float  # kg
    volume: float  # m^3
    com: Vec3  # meters, in the link-local frame
    inertia: InertiaTensor  # kg*m^2, about `com`, link-local axes
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
