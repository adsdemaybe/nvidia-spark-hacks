"""The electro-mechanical actuator model, at tier 0/1 cost (§3, v3).

The thing this exists to stop: a design that budgets torque against a datasheet
number taken at nominal voltage, passes, and then discovers on the bench that
four joints starting together pull the 11.1 V pack to 9.6 V, and every one of
them has 13% less torque than the report promised.

§3 states the model in one line — torque available is
`curve(voltage_at_motor) x ratio x eta`, where `voltage_at_motor` accounts for
battery sag and harness drop. This module is that line, and nothing more
expensive: no transient, no inductance, no PWM. `cosim/` already runs the real
SPICE-backed version at rollout cost; the point here is to fail an undersized
rail in under a millisecond, on every candidate, instead of surviving to a
MuJoCo rollout that assumed nominal voltage.

Three deliberate simplifications, each stated where it is used rather than
buried:

- **One pass, not a fixed point.** Current depends on voltage, which depends on
  current. We evaluate at worst-case (stall) current, which is the maximum, so
  the computed sag is the maximum and the computed torque is the minimum. The
  error is conservative by construction.
- **The gearbox is already in the catalogue entry.** `resolve_geared()` applies
  ratio and efficiency when composing a motor with a reducer, so `stall_torque`
  on a composed entry is output torque. Multiplying by the ratio again here
  would double-count it — a mistake worth naming, because the resulting numbers
  look plausible.
- **Steppers are not brushed motors.** The linear torque-speed line below is the
  brushed-DC/servo approximation. A stepper's torque falls off with speed
  through a quite different mechanism (winding inductance against commutation
  rate), so a stepper without a `torque_speed` table gets its holding torque
  back, unscaled, and `ActuatorOperatingPoint.notes` says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.catalogue import BatterySpec, MotorSpec, resolve as resolve_catalogue
from engine.ir import Electronics, Harness, Quantity, RobotIR, worst_provenance

# Resistivity of annealed copper at 20 C, ohm*m. A physical constant, not a
# design variable — it lives here so `harness_resistance` has no magic number.
COPPER_RESISTIVITY = 1.724e-8

# When a motor states no continuous rating, what fraction of its stall torque is
# safe to hold indefinitely. ASSUMED, and the criteria that use it say so: it is
# a rule of thumb, and the honest response to a motor without a continuous
# rating is to source one, not to trust this.
ASSUMED_CONTINUOUS_DERATE = 0.5


@dataclass(frozen=True)
class RailOperatingPoint:
    """What a rail actually delivers when everything on it is working at once."""

    rail_id: str
    nominal_v: float
    current_a: float  # worst-case draw the robot budgets
    pack_sag_v: float  # lost in the battery's internal resistance
    harness_drop_v: float  # lost in the cable to the load
    voltage_at_load_v: float
    provenance: str  # worst status among the inputs
    notes: tuple[str, ...] = ()

    @property
    def drop_fraction(self) -> float:
        return (self.nominal_v - self.voltage_at_load_v) / self.nominal_v if self.nominal_v else 0.0


@dataclass(frozen=True)
class ActuatorOperatingPoint:
    """Torque genuinely available at a joint, at a speed, on a real rail."""

    joint_id: str
    torque_nm: float
    speed_rad_s: float
    voltage_at_motor_v: float
    current_a: float
    provenance: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def harness_resistance(harness: Harness) -> float:
    """Round-trip resistance of one cable run, ohms.

    Round trip, not one-way. The current goes out on one conductor and back on
    another, and a drop calculation that counts only the outbound leg is exactly
    half the real answer — a 0.4 V error on a 12 V rail with four motors, which
    is the size of error that decides whether a design passes.
    """
    rho = (
        harness.resistivity.magnitude_in("ohm*m")
        if harness.resistivity is not None
        else COPPER_RESISTIVITY
    )
    length_m = harness.length.magnitude_in("m")
    area_m2 = harness.conductor_area.magnitude_in("m^2")
    if area_m2 <= 0:
        raise ValueError(f"harness {harness.id!r} has non-positive conductor area")
    return 2.0 * rho * length_m / area_m2


def motor_worst_case_current(motor: MotorSpec) -> tuple[float | None, str, str]:
    """Worst-case current one actuator draws, as `(amps, provenance, note)`.

    Returns `None` rather than a guess when the catalogue does not say. A rail
    budget computed with an unknown motor silently counted as 0 A is the failure
    mode this refuses: the criterion that calls this reports the gap instead of
    passing a design whose largest load was invisible.
    """
    if motor.stall_current is not None:
        return (
            motor.stall_current.value,
            motor.stall_current.provenance.status,
            "stall current from the catalogue",
        )
    if motor.rated_current is not None:
        # A bipolar stepper energises both phases at the rated per-phase current
        # when holding, so the supply sees roughly twice the phase figure.
        both_phases = motor.step_angle is not None
        factor = 2.0 if both_phases else 1.0
        return (
            motor.rated_current.value * factor,
            "INFERRED",
            (
                "no stall current in the catalogue; using rated current x2 phases "
                "(bipolar stepper holding)"
                if both_phases
                else "no stall current in the catalogue; using rated current"
            ),
        )
    return (None, "ASSUMED", f"{motor.key!r} states neither stall nor rated current")


def available_torque(
    motor: MotorSpec, *, voltage_v: float, speed_rad_s: float = 0.0
) -> tuple[float, str, tuple[str, ...]]:
    """Torque at the joint output at a given rail voltage and speed.

    Returns `(N*m, provenance, notes)`. See the module docstring for why the
    gearbox ratio is not applied here.
    """
    notes: list[str] = []
    stall = motor.stall_torque.value
    statuses = [motor.stall_torque.provenance.status]

    curve = motor.torque_speed
    if curve is not None:
        base = curve.torque_at(speed_rad_s)
        curve_v = curve.voltage.value
        statuses.append(curve.provenance.status)
        scaled = base * (voltage_v / curve_v) if curve_v > 0 else base
        notes.append(
            f"torque-speed table at {curve_v:.1f} V interpolated at {speed_rad_s:.2f} rad/s "
            f"({base:.4f} N*m), scaled to {voltage_v:.2f} V"
        )
        return max(scaled, 0.0), worst_provenance(*statuses), tuple(notes)

    if motor.rated_voltage is not None and motor.no_load_speed.value > 0:
        rated_v = motor.rated_voltage.value
        statuses.append(motor.rated_voltage.provenance.status)
        statuses.append(motor.no_load_speed.provenance.status)
        # Brushed-DC line: stall torque and no-load speed both scale with V, so
        # the slope dT/dw is voltage-independent and the whole line just shifts.
        slope = stall / motor.no_load_speed.value
        torque = stall * (voltage_v / rated_v) - slope * abs(speed_rad_s)
        notes.append(
            f"linear torque-speed line from {stall:.4f} N*m stall / "
            f"{motor.no_load_speed.value:.2f} rad/s no-load at {rated_v:.1f} V, "
            f"evaluated at {voltage_v:.2f} V"
        )
        # INFERRED at best: the line is a model of the motor, not a datasheet
        # figure, however confirmed its two endpoints are.
        return max(torque, 0.0), worst_provenance("INFERRED", *statuses), tuple(notes)

    notes.append(
        f"{motor.key!r} has neither a torque-speed table nor a rated voltage, so its "
        "stall torque could not be scaled to the actual rail voltage — the number "
        "below is the catalogue figure at its stated condition"
        + (f" ({motor.condition})" if motor.condition else "")
    )
    return stall, worst_provenance("ASSUMED", *statuses), tuple(notes)


def continuous_torque(motor: MotorSpec) -> tuple[float, str, str]:
    """What the motor can hold indefinitely, as `(N*m, provenance, note)`.

    Stall torque is a peak, not a duty. Sizing a joint that holds a payload all
    day against a stall figure is how a servo cooks itself in ten minutes while
    the report says it had 40% margin.
    """
    if motor.rated_torque is not None:
        return (
            motor.rated_torque.value,
            motor.rated_torque.provenance.status,
            "continuous rating from the catalogue",
        )
    return (
        motor.stall_torque.value * ASSUMED_CONTINUOUS_DERATE,
        "ASSUMED",
        f"no continuous rating in the catalogue; stall x {ASSUMED_CONTINUOUS_DERATE} "
        "as a rule of thumb — source the real figure before trusting this",
    )


def _joints_on_rail(ir: RobotIR, rail_id: str) -> list:
    if ir.electronics is None:
        return []
    return [
        j
        for j in ir.joints
        if j.actuator is not None and ir.electronics.joint_rail.get(j.id) == rail_id
    ]


def rail_operating_point(ir: RobotIR, rail_id: str) -> RailOperatingPoint:
    """Voltage a rail actually presents at the load, with everything drawing.

    "Everything drawing" is the worst case and it is the right one to size
    against: joints do not politely take turns, and a rover that starts four
    wheels simultaneously is the normal case, not the pathological one.
    """
    assert ir.electronics is not None, "rail_operating_point needs an electronics subsystem"
    elec: Electronics = ir.electronics
    rail = elec.rail(rail_id)
    nominal = rail.voltage.magnitude_in("V")
    statuses = [rail.voltage.provenance.status]
    notes: list[str] = []

    total_current = 0.0
    for joint in _joints_on_rail(ir, rail_id):
        motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
        amps, status, note = motor_worst_case_current(motor)
        statuses.append(status)
        if amps is None:
            notes.append(f"{joint.id}: {note} — its draw is missing from this budget")
            continue
        total_current += amps

    # Board loads. `pcb-ai` reports dissipation, not draw, so P/V is the best
    # available conversion and it is an overestimate of the load current on this
    # rail whenever a board is fed from more than one — noted, not hidden.
    for board in elec.boards:
        if rail_id in board.rails and board.measured_dissipation is not None:
            watts = board.measured_dissipation.magnitude_in("W")
            if nominal > 0:
                total_current += watts / nominal
                statuses.append(board.measured_dissipation.provenance.status)
                notes.append(
                    f"{board.id}: {watts:.2f} W measured dissipation counted as "
                    f"{watts / nominal:.3f} A on this rail"
                )

    r_pack = 0.0
    if rail.source_resistance is not None:
        r_pack = rail.source_resistance.magnitude_in("ohm")
        statuses.append(rail.source_resistance.provenance.status)
    elif elec.battery is not None:
        pack: BatterySpec = resolve_catalogue(elec.battery.catalogue, elec.battery.value)
        if pack.internal_resistance is not None:
            r_pack = pack.internal_resistance.magnitude_in("ohm")
            statuses.append(pack.internal_resistance.provenance.status)
        else:
            notes.append(f"pack {pack.key!r} states no internal resistance; sag assumed zero")
            statuses.append("ASSUMED")

    r_harness = 0.0
    harnesses = [h for h in elec.harnesses if h.rail == rail_id]
    if harnesses:
        # The longest run on the rail sets the worst voltage any load sees. An
        # average would flatter the design by hiding the joint at the end of the
        # cable, which is the one that browns out.
        worst = max(harnesses, key=harness_resistance)
        r_harness = harness_resistance(worst)
        statuses.append(worst.length.provenance.status)
        notes.append(f"harness drop taken on the worst run, {worst.id!r}")

    pack_sag = total_current * r_pack
    harness_drop = total_current * r_harness
    at_load = nominal - pack_sag - harness_drop

    return RailOperatingPoint(
        rail_id=rail_id,
        nominal_v=nominal,
        current_a=total_current,
        pack_sag_v=pack_sag,
        harness_drop_v=harness_drop,
        voltage_at_load_v=at_load,
        provenance=worst_provenance(*statuses),
        notes=tuple(notes),
    )


def actuator_operating_point(
    ir: RobotIR, joint_id: str, *, speed_rad_s: float = 0.0
) -> ActuatorOperatingPoint | None:
    """Torque at one joint, on the rail it is actually wired to.

    `None` when the robot has no electronics subsystem or the joint is not
    assigned a rail — the caller reports that as unmodelled rather than
    substituting a nominal-voltage number, which would be the whole failure this
    module exists to prevent.
    """
    if ir.electronics is None:
        return None
    rail_id = ir.electronics.joint_rail.get(joint_id)
    if rail_id is None:
        return None
    joint = next((j for j in ir.joints if j.id == joint_id), None)
    if joint is None or joint.actuator is None:
        return None

    motor: MotorSpec = resolve_catalogue(joint.actuator.catalogue, joint.actuator.value)
    rail_op = rail_operating_point(ir, rail_id)
    torque, torque_prov, notes = available_torque(
        motor, voltage_v=rail_op.voltage_at_load_v, speed_rad_s=speed_rad_s
    )
    amps, amps_prov, amps_note = motor_worst_case_current(motor)

    return ActuatorOperatingPoint(
        joint_id=joint_id,
        torque_nm=torque,
        speed_rad_s=speed_rad_s,
        voltage_at_motor_v=rail_op.voltage_at_load_v,
        current_a=amps if amps is not None else 0.0,
        provenance=worst_provenance(torque_prov, amps_prov, rail_op.provenance),
        notes=tuple(notes) + (amps_note,) + rail_op.notes,
    )


@dataclass(frozen=True)
class EnergyBudget:
    """Runtime and peak draw against one pack, for the tier-0 energy check."""

    pack_key: str
    usable_energy_wh: float
    average_power_w: float
    runtime_s: float
    peak_current_a: float
    pack_peak_current_a: float
    provenance: str
    notes: tuple[str, ...] = ()


def energy_budget(ir: RobotIR) -> EnergyBudget | None:
    """Runtime vs. the mission's duty cycle, and peak draw vs. the C-rating.

    §3: "trivial arithmetic, catches the 'great robot, 4-minute battery' class".
    It is trivial and it is also the check nobody runs until the robot exists.
    """
    if ir.electronics is None or ir.electronics.battery is None:
        return None
    elec = ir.electronics
    pack: BatterySpec = resolve_catalogue(elec.battery.catalogue, elec.battery.value)

    statuses = [pack.capacity.provenance.status, pack.nominal_voltage.provenance.status]
    notes: list[str] = []

    peak_current = 0.0
    for rail in elec.rails:
        op = rail_operating_point(ir, rail.id)
        peak_current += op.current_a
        statuses.append(op.provenance)

    # Duty cycle turns a peak into an average. It is ASSUMED unless a mission
    # profile says otherwise, and the runtime number is worth exactly that.
    duty = elec.mission_duty
    notes.append(f"mission duty cycle {duty:.2f} (ASSUMED unless a mission profile set it)")
    statuses.append("ASSUMED")

    usable_wh = (
        pack.capacity.magnitude_in("A*h")
        * pack.nominal_voltage.magnitude_in("V")
        * pack.usable_fraction
    )
    average_power = peak_current * pack.nominal_voltage.magnitude_in("V") * duty
    # Zero, not infinity, when nothing on the robot could be priced. An infinite
    # runtime is not a flattering result, it is a missing one: every load lacked
    # a current figure. Reporting `inf` made the criterion pass and made
    # coverage analysis report infinite sensitivity to a motor swap, which is
    # the two ways one sentinel value can lie at once.
    runtime_s = (usable_wh / average_power * 3600.0) if average_power > 0 else 0.0
    if average_power <= 0:
        notes.append(
            "no load on this robot has a current figure in the catalogue, so the "
            "average draw is zero and the runtime is unknown, not unlimited"
        )

    return EnergyBudget(
        pack_key=pack.key,
        usable_energy_wh=usable_wh,
        average_power_w=average_power,
        runtime_s=runtime_s,
        peak_current_a=peak_current,
        pack_peak_current_a=pack.peak_current,
        provenance=worst_provenance(*statuses),
        notes=tuple(notes),
    )


def quantity_v(value: float, note: str) -> Quantity:  # pragma: no cover - convenience
    from engine.ir import Provenance

    return Quantity(value=value, unit="V", provenance=Provenance(status="INFERRED", source="engine.electrical", note=note))
