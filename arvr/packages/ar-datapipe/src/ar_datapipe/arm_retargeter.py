"""ArmRetargeter — Shadow Robot Spatial Demonstration Pipeline spec section
26. A layer on top of `IkSolver`, not a replacement for it: the wrist pose
IS the end-effector target (the same 1:1 mapping `pipeline.py` already uses
for controller poses, spec section 8's datapipe step 1), and pinch aperture
becomes the gripper command. `IkSolver.solve()` itself is untouched.

This module imports fine on every platform (no direct pinocchio import) --
only calling `ArmRetargeter.step()` needs a real `IkSolver` instance, which
already requires Linux to have been constructed in the first place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from ar_contracts import IDENTITY_QUATERNION, HandFrame, OrientationXYZW, PositionM

from .retarget import IkSolver

# Mirrors xr-web/src/hands.ts's gripperFromAperture exactly -- necessary
# constant duplication, same accepted drift-risk as the TS/Python contract
# mirror documented in docs/CONTRACTS.md.
PINCH_CLOSED_M = 0.015
PINCH_OPEN_M = 0.08

IkStatus = Literal["ok", "failed", "joint_limit"]


@dataclass(frozen=True)
class ArmRetargetResult:
    ee_target_position_m: PositionM
    ee_target_orientation_xyzw: OrientationXYZW
    q: tuple[float, ...]
    dq: tuple[float, ...]
    joint_names: tuple[str, ...]
    gripper: float
    ik_status: IkStatus


def _gripper_from_aperture(aperture_m: float | None) -> float:
    if aperture_m is None or not math.isfinite(aperture_m):
        return 0.0
    t = (PINCH_OPEN_M - aperture_m) / (PINCH_OPEN_M - PINCH_CLOSED_M)
    return min(1.0, max(0.0, t))


def _pinch_aperture_m(hand: HandFrame) -> float | None:
    thumb = hand.joints.get("thumb-tip")
    index = hand.joints.get("index-finger-tip")
    if thumb is None or index is None:
        return None
    return math.dist(thumb.position_m, index.position_m)


class ArmRetargeter:
    """Stateful across a demo: keeps the previous joint solution as the IK
    warm-start (reuses IkSolver's post-convergence nullspace refinement for
    free -- see retarget.py) and previous timestamp for a real dq estimate,
    not just q - q_prev."""

    def __init__(self, solver: IkSolver) -> None:
        self.solver = solver
        self._q_prev: np.ndarray | None = None
        self._prev_timestamp_ns: int | None = None
        self._last_target: tuple[PositionM, OrientationXYZW] | None = None

    def step(self, hand: HandFrame, *, timestamp_ns: int) -> ArmRetargetResult:
        wrist = hand.joints.get("wrist")
        gripper = _gripper_from_aperture(_pinch_aperture_m(hand))

        if wrist is None:
            # Tracking dropout: never fabricate a target. Hold the last
            # known joint solution (or, if there has never been one, a
            # zero configuration) and report the failure honestly rather
            # than silently reusing a stale target as if it were fresh.
            joint_names = self.solver.joint_names
            q_hold = self._q_prev if self._q_prev is not None else np.zeros(len(joint_names))
            target_pos, target_quat = self._last_target or (
                (0.0, 0.0, 0.0),
                IDENTITY_QUATERNION,
            )
            return ArmRetargetResult(
                ee_target_position_m=target_pos,
                ee_target_orientation_xyzw=target_quat,
                q=tuple(float(x) for x in q_hold),
                dq=tuple(0.0 for _ in q_hold),
                joint_names=joint_names,
                gripper=gripper,
                ik_status="failed",
            )

        result = self.solver.solve(
            wrist.position_m, wrist.orientation_xyzw, q_init=self._q_prev,
        )
        q = np.array(result.joint_positions)

        dq = np.zeros_like(q)
        if self._q_prev is not None and self._prev_timestamp_ns is not None:
            dt_s = (timestamp_ns - self._prev_timestamp_ns) / 1_000_000_000
            if dt_s > 0:
                dq = (q - self._q_prev) / dt_s

        self._q_prev = q
        self._prev_timestamp_ns = timestamp_ns
        self._last_target = (wrist.position_m, wrist.orientation_xyzw)

        if not result.converged:
            ik_status: IkStatus = "failed"
        elif not result.within_limits:
            ik_status = "joint_limit"
        else:
            ik_status = "ok"

        return ArmRetargetResult(
            ee_target_position_m=wrist.position_m,
            ee_target_orientation_xyzw=wrist.orientation_xyzw,
            q=tuple(float(x) for x in q),
            dq=tuple(float(x) for x in dq),
            joint_names=result.joint_names,
            gripper=gripper,
            ik_status=ik_status,
        )
