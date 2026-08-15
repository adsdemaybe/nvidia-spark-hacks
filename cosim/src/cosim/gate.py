"""M5 — the gate.

A rollout that ran is not a rollout that passed. This turns a trajectory into a verdict,
using the same contract as every other stage in the project: a machine-checkable
predicate, measured, with failure reported rather than narrated.

Four questions, and they fail for different reasons, which is why they are separate:

1. **Did the simulation hold together?** Explicit coupling can diverge. A diverged
   rollout is a failure of the *simulation*, not of the design, and reporting it as a
   design failure would blame a board for a solver problem.
2. **Did the robot do the task?** The joint reaches its commanded pose, within a
   tolerance, before a deadline.
3. **Did the electronics survive?** Peak current inside the driver's rating for the
   whole rollout — not on average, at the worst instant.
4. **Did it survive thermally?** Dissipation over the *duty cycle*, which is a different
   and usually smaller number than the DC one, and the reason a driver that fails a
   continuous-current check can still be fine in a robot that accelerates and coasts.

Provenance runs through all of it. A rollout whose motor constants are `ASSUMED` is
evidence of a shape, not a number, and the verdict says so instead of leaving the reader
to check the catalogue.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cosim.rollout import RolloutResult


@dataclass
class DriverLimits:
    """What the electronics can take. From the board, not from the motor."""

    name: str
    continuous_current_a: float
    peak_current_a: float
    r_ds_on_ohm: float
    """Junction rise per watt — the thermal resistance of the package as built."""
    thermal_c_per_w: float
    max_junction_c: float = 150.0
    ambient_c: float = 25.0


# Measured on rover-motor-driver rather than taken from a datasheet: tssop16 gives
# 6.96 mm² of pad copper and no thermal pad. The real DRV8833 is an HTSSOP-16 PowerPAD
# part and would be far better; this is what the board as drawn actually achieves.
#
# 180 °C/W is reproducible rather than remembered:
#
#     npx tsx src/cli.ts --seed examples/rover-motor-driver.tsx --model stub \
#         --operating-point runs/rover-motor-driver/iter-0/operating-point.json
#
# reports "U1: 79.0°C at 0.300W" against a 25 °C ambient — a 54.0 °C rise, so 180 °C/W.
#
# It was 190 here for a while, carried over from an earlier solve, and F1's own physics
# stage read 140 °C/W at the same time. All three were the same package: the gap was that
# the PCB solver averaged the temperature field across U1's whole outline while the
# junction limit applies to the hotspot under the die. Fixing that put F1 at 180 and this
# constant now quotes it. Two independent models agreeing to 5% is the point of having
# both — they disagreed by 36% and that disagreement was the bug.
DRV8833_AS_BUILT = DriverLimits(
    name="DRV8833 in tssop16, no thermal pad",
    continuous_current_a=1.5,
    peak_current_a=2.0,
    r_ds_on_ohm=0.72,
    thermal_c_per_w=180.0,
)


@dataclass
class TaskGoal:
    """What the robot was asked to do."""

    joint_angle_rad: float
    tolerance_rad: float = math.radians(5)
    deadline_s: float = 1.0
    """Require it to still be there at the end, not merely to pass through."""
    must_settle: bool = True
    settle_vel_rad_s: float = 0.5


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class Verdict:
    checks: list[Check] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.blocking)

    def summary(self) -> str:
        lines = [f"ROLLOUT {'PASS' if self.passed else 'FAIL'}"]
        for c in self.checks:
            mark = "pass" if c.passed else ("FAIL" if c.blocking else "warn")
            lines.append(f"  {mark:4}  {c.name}: {c.detail}")
        if self.assumptions:
            lines.append("")
            lines.append("  resting on:")
            for a in self.assumptions:
                lines.append(f"    {a}")
        return "\n".join(lines)


def evaluate(
    result: RolloutResult,
    *,
    goal: TaskGoal | None = None,
    limits: DriverLimits = DRV8833_AS_BUILT,
    motor_provenance: str = "ASSUMED",
    duty_cycle: float | None = None,
) -> Verdict:
    """Turn a rollout into a verdict."""
    v = Verdict()

    # 1. Did the simulation hold together? Everything downstream is meaningless if not,
    #    so this is checked first and the rest is skipped rather than reported on a
    #    trajectory that is already fiction.
    if result.diverged:
        v.checks.append(
            Check(
                "coupling stable",
                False,
                f"the rollout diverged — {result.divergence_reason}. This is a simulation "
                "failure, not a design failure: shorten the control period before drawing "
                "any conclusion about the board or the robot",
            )
        )
        return v
    if not result.frames:
        v.checks.append(Check("coupling stable", False, "no frames were produced"))
        return v
    v.checks.append(
        Check(
            "coupling stable",
            True,
            f"{len(result.frames)} periods, {result.simulated_s:.3f} s simulated, no divergence",
        )
    )

    # 2. Did the robot do the task?
    if goal is not None:
        within = [
            f for f in result.frames
            if abs(f.joint_angle_rad - goal.joint_angle_rad) <= goal.tolerance_rad
        ]
        reached = within[0] if within else None
        last = result.frames[-1]
        settled = abs(last.joint_vel_rad_s) <= goal.settle_vel_rad_s
        at_goal = abs(last.joint_angle_rad - goal.joint_angle_rad) <= goal.tolerance_rad

        if reached is None:
            v.checks.append(
                Check(
                    "task",
                    False,
                    f"never reached {math.degrees(goal.joint_angle_rad):.1f}° "
                    f"(±{math.degrees(goal.tolerance_rad):.1f}°); ended at "
                    f"{math.degrees(last.joint_angle_rad):+.1f}°",
                )
            )
        elif reached.t > goal.deadline_s:
            v.checks.append(
                Check(
                    "task",
                    False,
                    f"reached the pose at {reached.t:.3f} s, past the {goal.deadline_s:.3f} s deadline",
                )
            )
        elif goal.must_settle and not (at_goal and settled):
            # Passing through the target on the way past is not arriving at it. A joint
            # that overshoots and keeps going has not done the task.
            v.checks.append(
                Check(
                    "task",
                    False,
                    f"reached {math.degrees(goal.joint_angle_rad):.1f}° at {reached.t:.3f} s but "
                    f"did not stay: ended at {math.degrees(last.joint_angle_rad):+.1f}° moving "
                    f"{last.joint_vel_rad_s:+.2f} rad/s",
                )
            )
        else:
            v.checks.append(
                Check(
                    "task",
                    True,
                    f"reached {math.degrees(goal.joint_angle_rad):.1f}° at {reached.t:.3f} s "
                    f"(deadline {goal.deadline_s:.3f} s) and held it",
                )
            )

    # 3. Did the electronics survive? The worst instant, not the average.
    peak = result.peak_current_a
    v.checks.append(
        Check(
            "electrical survival",
            peak <= limits.peak_current_a,
            f"peak {peak:.3f} A against a {limits.peak_current_a:.2f} A rating for "
            f"{limits.name}"
            + ("" if peak <= limits.peak_current_a else " — the driver would be damaged"),
        )
    )

    # 4. Thermal, over the duty cycle actually commanded.
    #
    # Computed from the rollout's own currents rather than from a nameplate figure: that
    # is the entire point of having simulated it. Two channels, I²R each.
    currents = [abs(f.current_a) for f in result.frames]
    rms = math.sqrt(sum(i * i for i in currents) / len(currents))
    observed_duty = sum(f.duty for f in result.frames) / len(result.frames)
    power_w = 2 * rms * rms * limits.r_ds_on_ohm
    junction_c = limits.ambient_c + power_w * limits.thermal_c_per_w
    v.checks.append(
        Check(
            "thermal (duty cycle)",
            junction_c <= limits.max_junction_c,
            f"{rms:.3f} A RMS at {observed_duty * 100:.0f}% mean duty → {power_w:.3f} W → "
            f"{junction_c:.1f} °C junction against a {limits.max_junction_c:.0f} °C limit "
            f"({limits.thermal_c_per_w:.0f} °C/W as built)",
        )
    )

    # 5. Provenance. Not blocking — an assumed constant does not make a rollout wrong,
    #    it makes the number un-quotable, and the difference matters.
    if motor_provenance != "CONFIRMED":
        v.checks.append(
            Check(
                "provenance",
                False,
                f"motor constants are {motor_provenance}: this rollout shows the shape of the "
                "answer, not a number to quote. Pick a real part before believing the margins",
                blocking=False,
            )
        )
        v.assumptions.append(
            f"motor R, L and Kt are {motor_provenance} — no motor has been chosen"
        )
    else:
        v.checks.append(Check("provenance", True, "motor constants are CONFIRMED", blocking=False))

    v.assumptions.append(
        f"{limits.name}: {limits.thermal_c_per_w:.0f} °C/W measured from the board's own copper"
    )
    if duty_cycle is not None and abs(duty_cycle - observed_duty) > 0.05:
        v.assumptions.append(
            f"the rollout ran at {observed_duty*100:.0f}% duty, not the {duty_cycle*100:.0f}% "
            "the thermal budget assumed"
        )
    return v
