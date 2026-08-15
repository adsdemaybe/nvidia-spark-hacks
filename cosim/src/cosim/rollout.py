"""The closed loop — electrical and mechanical, stepping together.

This is M4, and the plan calls it the risky milestone for a specific reason: the coupling
is **explicit**. The electrical side solves a period with ω held at whatever the
mechanics last reported, then the mechanics advances with torque held at whatever the
electrical side just produced. Neither sees the other change during its own step.

That is standard Jacobi co-simulation and it is stable only while the exchange period is
short against both time constants. When it is not, the two halves ping-pong: a slightly
high torque produces a slightly high speed, which produces a slightly low torque, which
produces a lower speed, and the oscillation grows.

So this reports divergence rather than smoothing it. `RolloutResult.diverged` is a real
outcome, and the gate treats it as a failure of the *simulation*, not of the design —
those are different things and conflating them would blame a board for a solver problem.

Run it in one process rather than three: the bus is proven separately by its own tests,
and putting a socket between two function calls here would add a failure mode without
adding a fact. `bus_rollout()` is the distributed version, for when participants really
are separate processes.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from cosim.electrical import OperatingPoint, Surface, SpiceBackend
from cosim.mjcf import Mechanics
from cosim.robot import RobotSpec


@dataclass
class Frame:
    seq: int
    t: float
    duty: float
    omega_shaft_rad_s: float
    current_a: float
    current_peak_a: float
    torque_nm: float
    joint_angle_rad: float
    joint_vel_rad_s: float


@dataclass
class RolloutResult:
    frames: list[Frame] = field(default_factory=list)
    diverged: bool = False
    divergence_reason: str = ""
    wall_s: float = 0.0
    simulated_s: float = 0.0
    peak_current_a: float = 0.0

    @property
    def realtime_factor(self) -> float:
        return self.simulated_s / self.wall_s if self.wall_s > 0 else float("inf")

    def summary(self) -> str:
        if not self.frames:
            return "rollout: no frames"
        last = self.frames[-1]
        lines = [
            f"rollout: {len(self.frames)} periods, {self.simulated_s:.3f} s simulated "
            f"in {self.wall_s:.2f} s wall ({self.realtime_factor:.2f}x real time)",
            f"  final: joint {math.degrees(last.joint_angle_rad):+.1f}° at "
            f"{last.joint_vel_rad_s:+.2f} rad/s, motor {last.omega_shaft_rad_s:+.0f} rad/s",
            f"  current: {last.current_a:.3f} A at the end, {self.peak_current_a:.3f} A peak",
        ]
        if self.diverged:
            lines.append(f"  DIVERGED — {self.divergence_reason}")
        return "\n".join(lines)


class ElectricalModel:
    """Whatever can answer "torque, given duty and ω"."""

    def evaluate(self, duty: float, omega_rad_s: float) -> OperatingPoint:  # pragma: no cover
        raise NotImplementedError


class SurfaceModel(ElectricalModel):
    def __init__(self, surface: Surface):
        self.surface = surface

    def evaluate(self, duty: float, omega_rad_s: float) -> OperatingPoint:
        # Direction is handled by the caller; the surface is characterised for forward
        # drive and a reversed motor is the mirror image.
        sign = 1.0 if omega_rad_s >= 0 else -1.0
        return self.surface.evaluate(duty, abs(omega_rad_s)) if sign > 0 else self.surface.evaluate(
            duty, abs(omega_rad_s)
        )


class DirectModel(ElectricalModel):
    def __init__(self, backend: SpiceBackend):
        self.backend = backend

    def evaluate(self, duty: float, omega_rad_s: float) -> OperatingPoint:
        return self.backend.evaluate(duty, abs(omega_rad_s))


# Divergence guards. Generous, because they exist to catch a solver blowing up rather
# than to police design choices — a rollout that legitimately reaches these is telling
# you something about the model, not about the robot.
MAX_SHAFT_RAD_S = 1.0e5
MAX_CURRENT_A = 1.0e3


def rollout(
    spec: RobotSpec,
    electrical: ElectricalModel,
    *,
    motor_id: str = "M1",
    duty_of: "callable[[int, float, float], float]" = lambda seq, t, angle: 1.0,
    periods: int = 500,
    dt_s: float = 0.001,
    substeps: int | None = None,
) -> RolloutResult:
    """Step the loop.

    `duty_of(seq, t, joint_angle)` is the firmware: whatever decides what the PWM should
    be this period. A constant is fine for a spin-up test; a controller closes the outer
    loop.

    `substeps` defaults to however many MuJoCo steps fit in one control period, so the
    mechanical integration stays at its own natural timestep rather than being stretched
    to match the control rate.
    """
    mech = Mechanics(spec)
    if substeps is None:
        substeps = max(1, round(dt_s / spec.timestep_s))

    result = RolloutResult()
    t0 = time.monotonic()
    t = 0.0

    for seq in range(periods):
        angle, joint_vel = mech.joint_state(spec.actuators[0].joint)
        omega_shaft = mech.motor_shaft_speed(motor_id)

        # The guard has to come before the electrical solve: feeding a diverged ω into
        # the surface produces a torque from a clamped edge, which looks plausible and
        # hides the fact that the simulation already failed.
        if not math.isfinite(omega_shaft) or abs(omega_shaft) > MAX_SHAFT_RAD_S:
            result.diverged = True
            result.divergence_reason = (
                f"shaft speed reached {omega_shaft:.3g} rad/s at period {seq} — the "
                "explicit coupling is unstable at this control period"
            )
            break

        duty = max(0.0, min(1.0, duty_of(seq, t, angle)))
        op = electrical.evaluate(duty, omega_shaft)

        if not math.isfinite(op.current_avg_a) or abs(op.current_avg_a) > MAX_CURRENT_A:
            result.diverged = True
            result.divergence_reason = (
                f"current reached {op.current_avg_a:.3g} A at period {seq}"
            )
            break

        # Torque opposes motion when the shaft is already turning faster than the drive
        # can sustain; the surface encodes that through back-EMF, so the sign follows the
        # commanded direction rather than being imposed here.
        mech.apply_motor_torque(motor_id, op.torque_nm)
        mech.step(substeps)

        angle, joint_vel = mech.joint_state(spec.actuators[0].joint)
        result.peak_current_a = max(result.peak_current_a, op.current_peak_a)
        result.frames.append(
            Frame(
                seq=seq,
                t=t,
                duty=duty,
                omega_shaft_rad_s=omega_shaft,
                current_a=op.current_avg_a,
                current_peak_a=op.current_peak_a,
                torque_nm=op.torque_nm,
                joint_angle_rad=angle,
                joint_vel_rad_s=joint_vel,
            )
        )
        t += dt_s

    result.wall_s = time.monotonic() - t0
    result.simulated_s = t
    return result
