"""Tier 0: the electronics boundary (§9, "new" in v3).

§9's demand is precise and easy to fail: "Coverage perturbation reaches through
the boundary: perturbing motor choice must move `rail_margin`, or the
electronics subsystem is BLIND and the integration is decorative."

So every criterion here exposes a magnitude that a motor swap actually moves —
a ratio or a clearance in millimetres, never a boolean. `rail_margin` is the
one that matters most: it is the number that connects "which motor" to "which
board", and if it does not respond to the motor, the two halves of the platform
are only nominally connected.

Everything here returns no results when `ir.electronics is None`. That is not a
pass. `evaluate()` reports the subsystem as unmodelled, which is §12
non-negotiable #5 ("a tier that didn't run is not a pass") applied one level
down — a robot nobody has powered has not passed its power checks.
"""

from __future__ import annotations

import numpy as np

from engine.criteria.base import CriterionResult
from engine.criteria.registry import register
from engine.electrical import (
    energy_budget,
    harness_resistance,
    motor_worst_case_current,
    rail_operating_point,
)
from engine.catalogue import MotorSpec, resolve as resolve_catalogue
from engine.ir import RobotIR, worst_provenance
from engine.mass_properties import MassProperties

# Still-air natural convection from a small plastic enclosure, W/(m^2*K). The
# textbook range for a vertical plate in free air is roughly 3-10; 6 is the
# middle of it. ASSUMED, loudly: this is the number that decides whether a
# thermal verdict means anything, and the honest answer is that it comes from a
# measurement on the real enclosure (Phase 3) or from `pcb-ai`'s solver with the
# boundary condition this side supplies (§7.3, integration step I3).
_CONVECTION_W_PER_M2K = 6.0
# Allowed rise of the enclosure wall above ambient before "the enclosure sheds
# it" stops being true. 25 K over a 25 C room puts the wall at 50 C — hot to
# touch, below PLA's glass transition, and the point where a designer should be
# told rather than a limit anyone measured.
_ALLOWED_RISE_K = 25.0

# Below this, a rail is over-committed. 1.2 rather than 1.0 because a budget met
# exactly has no room for the tolerance stack every one of its inputs carries.
_RAIL_MARGIN_TARGET = 1.2


@register("electronics_erc", tier=0)
def _electronics_erc(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Every actuated joint is on a rail (§2: "a motor with no driver rail is an
    ERC failure at the robot level, before `pcb-ai` ever runs").

    Catching it here rather than at PCB import is the entire point. A joint with
    no rail is not a board problem to discover during placement; it is a robot
    whose designer has not decided how that motor is powered, and the cheapest
    moment to say so is before anyone routes copper.
    """
    if ir.electronics is None:
        return []

    actuated = [j for j in ir.joints if j.actuator is not None and j.kind != "fixed"]
    if not actuated:
        return []

    assigned = [j for j in actuated if j.id in ir.electronics.joint_rail]
    unassigned = [j.id for j in actuated if j.id not in ir.electronics.joint_rail]
    fraction = len(assigned) / len(actuated)

    return [
        CriterionResult(
            name="electronics_erc",
            magnitude=fraction,
            passed=not unassigned,
            unit="ratio",
            detail=(
                f"{len(assigned)}/{len(actuated)} actuated joints assigned a rail"
                + (f"; unpowered: {sorted(unassigned)}" if unassigned else "")
            ),
            provenance="CONFIRMED",  # a topology fact about the IR, not a measurement
        )
    ]


@register("board_gate_passed", tier=0)
def _board_gate_passed(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """§12 non-negotiable #10, enforced where every other verdict is enforced.

    "A board gate failure is a robot failure. No robot-side agent — chief
    included — can waive a `pcb-ai` hard failure." Putting it here rather than
    raising at ingest time is what makes it unwaivable: an exception can be
    caught by whoever calls the ingest, a failing criterion cannot be caught by
    anything, because `evaluate()` is the only thing that returns a verdict and
    it does not take arguments about which failures to ignore.

    A board that has not been designed yet is `NOT_RUN`, and that is a fail —
    §12 #5, again: not having run is not a pass. Magnitude is the fraction of
    boards that have passed, so the number moves as boards land.
    """
    if ir.electronics is None or not ir.electronics.boards:
        return []

    boards = ir.electronics.boards
    passing = [b for b in boards if b.gate_status == "PASS"]
    failing = [b.id for b in boards if b.gate_status == "FAIL"]
    pending = [b.id for b in boards if b.gate_status == "NOT_RUN"]

    return [
        CriterionResult(
            name="board_gate_passed",
            magnitude=len(passing) / len(boards),
            passed=not failing and not pending,
            unit="ratio",
            detail=(
                f"{len(passing)}/{len(boards)} boards passed their pcb-ai gates"
                + (f"; FAILING: {sorted(failing)}" if failing else "")
                + (f"; never designed: {sorted(pending)}" if pending else "")
            ),
            provenance="MEASURED" if passing and not pending else "ASSUMED",
        )
    ]


@register("rail_margin", tier=0)
def _rail_margin(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Budgeted current over worst-case draw, as a ratio, per rail.

    The §9 criterion that must respond to a motor swap. It does, because the
    worst-case draw is summed from the stall currents of the actuators actually
    assigned to the rail — change the motor, change the number.
    """
    if ir.electronics is None:
        return []

    results: list[CriterionResult] = []
    for rail in ir.electronics.rails:
        op = rail_operating_point(ir, rail.id)
        budget = rail.budget_current.magnitude_in("A")
        if op.current_a <= 0.0:
            # Not a pass: a rail with nothing on it that we can price is a rail
            # whose margin is unknown, and reporting 'infinite margin' would be
            # the most flattering possible reading of missing data.
            results.append(
                CriterionResult(
                    name=f"rail_margin[{rail.id}]",
                    magnitude=0.0,
                    passed=False,
                    unit="ratio",
                    detail=(
                        f"no priceable load on {rail.id!r}: nothing is assigned to it, or "
                        "every assigned actuator lacks a current figure in the catalogue"
                        + (f" ({'; '.join(op.notes)})" if op.notes else "")
                    ),
                    provenance="ASSUMED",
                )
            )
            continue

        margin = budget / op.current_a
        results.append(
            CriterionResult(
                name=f"rail_margin[{rail.id}]",
                magnitude=margin,
                passed=bool(margin >= _RAIL_MARGIN_TARGET),
                unit="ratio",
                detail=(
                    f"budget={budget:.2f}A worst-case={op.current_a:.2f}A "
                    f"(target >= {_RAIL_MARGIN_TARGET}x); "
                    f"V at load {op.voltage_at_load_v:.2f} of {op.nominal_v:.2f} nominal"
                ),
                provenance=op.provenance,
            )
        )
    return results


@register("actuator_voltage_in_range", tier=0)
def _actuator_voltage_in_range(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """The voltage at the motor, against the window the motor is rated for.

    Added because coverage analysis reported the rail voltage as **BLIND**: a
    +/-10% perturbation of `v_motor` moved no criterion at all. §9 is explicit
    about what that means — "BLIND = no criterion responds (harness bug, needs a
    new criterion, not more search)" — and it was a harness bug, not a modelling
    subtlety. Nothing in the platform checked that the rail could actually run
    the motor bolted to it.

    The physical failure is ordinary and expensive: a 12 V servo on a 3S pack
    that sags to 9.4 V under load is below its stated minimum, so it browns out
    and resets mid-motion. Every other criterion passes, because torque, current
    and geometry are all individually fine.

    Magnitude is the distance into the window as a fraction of its width — 0.5
    is dead centre, 0 and 1 are the edges, outside is a fail. A ratio rather
    than a boolean, and one that moves continuously with the rail (§9).
    """
    if ir.electronics is None:
        return []

    results: list[CriterionResult] = []
    for joint in ir.joints:
        if joint.actuator is None:
            continue
        rail_id = ir.electronics.joint_rail.get(joint.id)
        if rail_id is None:
            continue
        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        if motor.voltage_min is None or motor.voltage_max is None:
            # Not a pass. A motor whose operating window the catalogue does not
            # state is a motor nobody checked the rail against, and saying so is
            # the point of §12 #5.
            results.append(
                CriterionResult(
                    name=f"actuator_voltage_in_range[{joint.id}]",
                    magnitude=0.0,
                    passed=False,
                    unit="ratio",
                    detail=(
                        f"{motor.key!r} states no voltage_min/voltage_max, so the rail "
                        "cannot be checked against it — source the operating window (§6.1)"
                    ),
                    provenance="ASSUMED",
                )
            )
            continue

        op = rail_operating_point(ir, rail_id)
        lo = motor.voltage_min.magnitude_in("V")
        hi = motor.voltage_max.magnitude_in("V")
        actual = op.voltage_at_load_v
        span = hi - lo
        position = (actual - lo) / span if span > 0 else 0.0

        results.append(
            CriterionResult(
                name=f"actuator_voltage_in_range[{joint.id}]",
                magnitude=position,
                passed=bool(lo <= actual <= hi),
                unit="ratio",
                detail=(
                    f"{actual:.2f}V at the motor (rail {rail_id!r} nominal "
                    f"{op.nominal_v:.2f}V, less {op.pack_sag_v:.2f}V pack sag and "
                    f"{op.harness_drop_v:.2f}V harness drop) against {motor.key!r}'s "
                    f"{lo:.1f}-{hi:.1f}V window"
                ),
                provenance=worst_provenance(
                    op.provenance,
                    motor.voltage_min.provenance.status,
                    motor.voltage_max.provenance.status,
                ),
            )
        )
    return results


@register("harness_drop", tier=0)
def _harness_drop(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Volts lost in the cable at stall, per harness run.

    §7.3: once Circuit JSON gives connector positions, harness lengths stop
    being guesses. Until then the length is whatever the IR says and the
    provenance on it says which of the two this is.

    Magnitude is the *drop*, so it is a quantity to minimise and the pass test
    is an upper bound. 5% of the rail is the conventional wiring limit and it is
    what a motor at the end of a long run actually feels.
    """
    if ir.electronics is None:
        return []

    results: list[CriterionResult] = []
    for harness in ir.electronics.harnesses:
        rail = ir.electronics.rail(harness.rail)
        nominal = rail.voltage.magnitude_in("V")
        resistance = harness_resistance(harness)

        # The current through *this* run, not the whole rail: a harness feeding
        # one motor carries one motor's stall current, and charging it with the
        # rail total would condemn every cable on a four-motor robot.
        current = 0.0
        statuses = [rail.voltage.provenance.status, harness.length.provenance.status]
        note = ""
        joint = next((j for j in ir.joints if j.id == harness.to), None)
        if joint is not None and joint.actuator is not None:
            motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
            amps, status, note = motor_worst_case_current(motor)
            statuses.append(status)
            current = amps or 0.0
        else:
            op = rail_operating_point(ir, harness.rail)
            current = op.current_a
            statuses.append(op.provenance)
            note = f"board-to-board run; charged with the whole {harness.rail!r} draw"

        drop = current * resistance
        limit = 0.05 * nominal
        results.append(
            CriterionResult(
                name=f"harness_drop[{harness.id}]",
                magnitude=drop,
                passed=bool(drop <= limit),
                unit="V",
                detail=(
                    f"{drop:.3f}V at {current:.2f}A through {resistance * 1000:.1f}mohm "
                    f"({harness.length.magnitude_in('m') * 1000:.0f}mm round trip), "
                    f"limit {limit:.3f}V (5% of {nominal:.1f}V); {note}"
                ),
                provenance=worst_provenance(*statuses),
            )
        )
    return results


@register("board_fits_bay", tier=0)
def _board_fits_bay(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Clearance in millimetres between the routed board and the bay it must sit in.

    Only fires for a board `pcb-ai` has actually designed. Before that there is
    nothing to compare against the envelope, and comparing an envelope to itself
    would be a criterion that passes by construction — the decorative-integration
    failure §9 warns about, in its purest form.

    Magnitude is the *tightest* clearance in mm rather than a boolean, so a 0.2 mm
    interference and a 40 mm one are distinguishable — the same reason `pcb-ai`'s
    own `Violation` carries measured/limit instead of a flag.
    """
    if ir.electronics is None:
        return []

    results: list[CriterionResult] = []
    for board in ir.electronics.boards:
        if board.measured_outline is None:
            continue
        clearances = [
            board.max_outline.x - board.measured_outline.x,
            board.max_outline.y - board.measured_outline.y,
        ]
        axes = ["x", "y"]
        if board.measured_max_component_height is not None:
            clearances.append(
                board.max_component_height.magnitude_in("mm")
                - board.measured_max_component_height.magnitude_in("mm")
            )
            axes.append("height")

        tightest = min(clearances)
        axis = axes[int(np.argmin(clearances))]
        results.append(
            CriterionResult(
                name=f"board_fits_bay[{board.id}]",
                magnitude=tightest,
                passed=bool(tightest >= 0.0),
                unit="mm",
                detail=(
                    f"tightest clearance {tightest:+.2f}mm on {axis}; "
                    f"board {board.measured_outline.x:.1f}x{board.measured_outline.y:.1f}mm "
                    f"in a {board.max_outline.x:.1f}x{board.max_outline.y:.1f}mm bay "
                    f"(run {board.run_dir or 'unrecorded'})"
                ),
                # A routed outline is measured off the artifact by an independent
                # gated pipeline — §5's MEASURED class.
                provenance="MEASURED",
            )
        )
    return results


@register("board_thermal_budget", tier=0)
def _board_thermal_budget(
    ir: RobotIR, mass_props: dict[str, MassProperties]
) -> list[CriterionResult]:
    """Board dissipation against what the enclosure around it can shed.

    This is the robot side of the loop §7.3 describes: `pcb-ai`'s thermal model
    needs a boundary condition only the robot knows (enclosed? airflow?), and
    the robot needs the dissipation only the board knows. Until integration step
    I3 closes that loop properly, this is the cheap version — natural convection
    off the bay link's surface, with both constants ASSUMED and named.

    The provenance is therefore ASSUMED however MEASURED the dissipation is, and
    that is the correct answer: a verdict is worth its weakest input.
    """
    if ir.electronics is None:
        return []

    results: list[CriterionResult] = []
    for board in ir.electronics.boards:
        if board.measured_dissipation is None:
            continue
        watts = board.measured_dissipation.magnitude_in("W")

        mp = mass_props.get(board.mounted_on)
        if mp is None:
            continue
        lo, hi = mp.bbox_min, mp.bbox_max
        dx, dy, dz = hi.x - lo.x, hi.y - lo.y, hi.z - lo.z
        area_m2 = 2.0 * (dx * dy + dy * dz + dx * dz)
        shed_w = _CONVECTION_W_PER_M2K * area_m2 * _ALLOWED_RISE_K

        margin = (shed_w - watts) / shed_w if shed_w > 0 else -1.0
        results.append(
            CriterionResult(
                name=f"board_thermal_budget[{board.id}]",
                magnitude=margin,
                passed=bool(watts <= shed_w),
                unit="ratio",
                detail=(
                    f"{watts:.2f}W dissipated into a {area_m2 * 1e4:.0f}cm^2 "
                    f"{board.mounted_on!r} envelope that sheds ~{shed_w:.2f}W at "
                    f"h={_CONVECTION_W_PER_M2K} W/m^2K and {_ALLOWED_RISE_K}K rise "
                    "(both ASSUMED — replace with pcb-ai's solve at integration step I3)"
                ),
                provenance="ASSUMED",
            )
        )
    return results


@register("energy_runtime", tier=0)
def _energy_runtime(ir: RobotIR, mass_props: dict[str, MassProperties]) -> list[CriterionResult]:
    """Runtime against the mission, and peak draw against the pack's C-rating.

    §3's energy tier. "Trivial arithmetic, catches the 'great robot, 4-minute
    battery' class" — and the second half catches the class nobody expects,
    where the pack has ample capacity and simply cannot deliver the current: a
    6.8 Ah Li-ion pack rated 20 A continuous is generous on runtime and fails
    outright the moment four steppers stall together.
    """
    budget = energy_budget(ir)
    if budget is None:
        return []

    results: list[CriterionResult] = []

    target_s = None
    if ir.electronics is not None and ir.electronics.mission_duration is not None:
        target_s = ir.electronics.mission_duration.magnitude_in("s")

    if target_s is not None:
        magnitude = budget.runtime_s / target_s if target_s > 0 else 0.0
        passed = magnitude >= 1.0
        detail = (
            f"{budget.runtime_s / 60:.1f} min against a {target_s / 60:.1f} min mission "
            f"({budget.average_power_w:.1f}W average from {budget.usable_energy_wh:.1f}Wh usable)"
        )
    else:
        # No stated mission, so there is no ratio to pass. Report the runtime as
        # the magnitude and pass it — but say plainly that nothing was compared,
        # because "passed" here means "not contradicted", not "sufficient".
        magnitude = budget.runtime_s / 60.0
        passed = budget.runtime_s > 0
        detail = (
            f"{budget.runtime_s / 60:.1f} min at {budget.average_power_w:.1f}W average; "
            "no mission_duration stated, so nothing was compared against it"
        )

    results.append(
        CriterionResult(
            name="energy_runtime",
            magnitude=magnitude,
            passed=bool(passed),
            unit="ratio" if target_s is not None else "min",
            detail=f"pack {budget.pack_key!r}: {detail}; {'; '.join(budget.notes)}",
            provenance=budget.provenance,
        )
    )

    c_margin = (
        budget.pack_peak_current_a / budget.peak_current_a if budget.peak_current_a > 0 else 0.0
    )
    results.append(
        CriterionResult(
            name="peak_draw_within_c_rating",
            magnitude=c_margin,
            passed=bool(c_margin >= 1.0),
            unit="ratio",
            detail=(
                f"pack delivers {budget.pack_peak_current_a:.1f}A continuous, "
                f"worst-case draw is {budget.peak_current_a:.1f}A"
            ),
            provenance=budget.provenance,
        )
    )
    return results
