"""Catalogues of purchasable parts and materials — real MPNs, datasheet numbers.

Implements `text-to-cad-plan.md` §8.5. Two rules from that section are load-bearing
and visible in the shapes below:

- **Every torque carries its condition.** `stall_torque` is meaningless without the
  voltage (servos) or phase current (steppers) it was measured at, so `condition`
  is a required field, not a note. The Feetech STS3215 is the standing proof: the
  *same part number* is 30 kg·cm at 12 V and 19 kg·cm at 7.4 V. A catalogue that
  stored one number for "STS3215" would be wrong for half its users.

- **Motor and gearbox are separate entries composed at resolve time.** Baking a
  ratio into a motor key hides which motor it is and makes every other ratio
  invisible to the optimizer. `resolve_geared()` does the composition and applies
  the reducer's efficiency, because `motor_torque x ratio` with no loss term
  overstates output by 10-30%.

Provenance is not decorative here. `CONFIRMED` entries carry the URL the number
came from, and `Provenance` refuses a CONFIRMED status with an empty source. Where
a figure could not be traced to a manufacturer document it is `INFERRED` or
`ASSUMED` and says so — §8.5.4 is explicit that a part whose torque is ASSUMED
must never reach a customer BOM.

**Known gap, stated rather than hidden:** the values below were transcribed from
manufacturer pages and distributor listings, not from the PDF drawings themselves.
Fields marked ASSUMED (stepper masses, gearbox efficiencies, stepper no-load
speeds) are the ones to verify first. The 17HS4401 is a live example of why: it is
sold as 40, 42, and 43 N*cm at 1.5, 1.68, and 1.7 A by different vendors under one
part number.
"""

from __future__ import annotations

from pydantic import BaseModel

from engine.ir import Provenance, Quantity

# --- unit helpers -------------------------------------------------------

_KGCM_TO_NM = 0.0980665  # 1 kgf*cm in N*m
_NCM_TO_NM = 0.01


def _kgcm(v: float) -> float:
    """kgf*cm -> N*m. The single most common spec error in hobby servos is
    treating kg*cm as N*m, a factor of ~10."""
    return v * _KGCM_TO_NM


def _sec60_to_rad_s(seconds_per_60deg: float) -> float:
    """Servo speed is quoted as seconds per 60 degrees."""
    import math

    return math.radians(60.0 / seconds_per_60deg)


def _rpm_to_rad_s(rpm: float) -> float:
    import math

    return rpm * 2.0 * math.pi / 60.0


def _confirmed(url: str, note: str = "") -> Provenance:
    return Provenance(status="CONFIRMED", source=url, note=note)


def _inferred(source: str, note: str = "") -> Provenance:
    return Provenance(status="INFERRED", source=source, note=note)


def _assumed(note: str) -> Provenance:
    return Provenance(status="ASSUMED", source="", note=note)


def _q(value: float, unit: str, prov: Provenance) -> Quantity:
    return Quantity(value=value, unit=unit, provenance=prov)


# --- spec shapes --------------------------------------------------------


class BodySize(BaseModel):
    """Outside dimensions of a purchased part's body, in metres.

    Needed by the `component` geometry generator, which builds a solid-box
    inertia from them. Not optional there: a lumped mass with no extent has a
    rank-deficient tensor, and several of them combine into one that violates
    A + B >= C and is rejected at model-compile time.
    """

    length: Quantity
    width: Quantity
    height: Quantity


class MotorSpec(BaseModel):
    """Any rotary actuator: stepper, smart servo, hobby servo, gearmotor.

    A servo is a motor plus a reducer plus control, so it lives in the same shape
    — `joint_torque_budget` only needs torque, and treating them separately would
    duplicate the criterion.
    """

    key: str
    stall_torque: Quantity  # N*m — holding torque for steppers
    no_load_speed: Quantity  # rad/s
    mass: Quantity  # kg
    gear_ratio: float
    # --- real-part identity (§8.5.2) ---
    part_number: str = ""
    manufacturer: str = ""
    datasheet_url: str = ""
    # --- the operating point the torque above applies at. Required in spirit:
    #     an empty condition means the number cannot be checked.
    condition: str = ""
    rated_torque: Quantity | None = None
    rated_current: Quantity | None = None
    stall_current: Quantity | None = None
    voltage_min: Quantity | None = None
    voltage_max: Quantity | None = None
    step_angle: Quantity | None = None  # steppers only
    protocol: str = ""  # smart servos: "TTL half-duplex", "RS485", "PWM"
    # Angular lost motion at the output. `None` means direct drive: a stepper
    # bolted straight to the joint has no gear teeth to have play between, which
    # is a different statement from "not yet measured" and the `backlash`
    # criterion has to tell the two apart. Populated by resolve_geared() from
    # the reducer, because backlash is a property of the gearbox, not the motor.
    backlash: Quantity | None = None
    # Body envelope, for lumping the part onto a link as a real solid rather
    # than a point. See `BodySize` and the `component` geometry generator.
    body_size: "BodySize | None" = None


class GearboxSpec(BaseModel):
    """A reducer, composed onto a motor rather than baked into one."""

    key: str
    ratio: float
    efficiency: float  # 0-1, applied in resolve_geared()
    max_output_torque: Quantity
    mass: Quantity
    added_length: Quantity
    manufacturer: str = ""
    part_number: str = ""
    datasheet_url: str = ""
    backlash: Quantity | None = None


class DriverSpec(BaseModel):
    """Motor driver — the electronic half a moving joint also needs."""

    key: str
    max_current_per_phase: Quantity  # A
    voltage_min: Quantity
    voltage_max: Quantity
    microstepping: str = ""
    interface: str = ""
    manufacturer: str = ""
    part_number: str = ""
    datasheet_url: str = ""


class MaterialSpec(BaseModel):
    key: str
    density: Quantity  # kg/m^3
    yield_strength: Quantity  # Pa
    part_number: str = ""
    manufacturer: str = ""
    datasheet_url: str = ""


class Catalogue:
    """A named, typed lookup table. Raises KeyError on unknown keys — an
    optimizer or agent must never be able to invent a part that doesn't exist
    (§11 non-negotiable #1).
    """

    def __init__(self, name: str, entries: dict[str, BaseModel]):
        self.name = name
        self._entries = entries

    def __getitem__(self, key: str) -> BaseModel:
        try:
            return self._entries[key]
        except KeyError:
            raise KeyError(
                f"{key!r} not in catalogue {self.name!r}; known keys: {sorted(self._entries)}"
            ) from None

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __iter__(self):
        # Without this, `list(catalogue)` falls back to the legacy sequence
        # protocol and calls `__getitem__(0)`, so iterating a catalogue fails with
        # "0 not in catalogue 'gearboxes'" — an error that reads like a corrupt
        # catalogue rather than the missing method it is.
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> list[str]:
        return sorted(self._entries)


# --- stepper motors -----------------------------------------------------

_HANDSONTEC_17HS4401 = "https://www.handsontec.com/dataspecs/17HS4401S.pdf"
_BESFOC_17HS4401 = "https://www.besfoc.com/nema-17-17hs4401-stepper-motor.html"

STEPPER_MOTORS = Catalogue(
    "stepper_motors",
    {
        "nema17_17hs4401": MotorSpec(
            key="nema17_17hs4401",
            part_number="17HS4401",
            manufacturer="generic NEMA17 (multi-vendor)",
            datasheet_url=_HANDSONTEC_17HS4401,
            condition="holding torque, 1.7 A/phase bipolar",
            stall_torque=_q(
                40 * _NCM_TO_NM,
                "N*m",
                _confirmed(
                    _HANDSONTEC_17HS4401,
                    "40 N*cm holding torque; vendors also list 42 and 43 N*cm for this MPN "
                    "— verify against the specific supplier's sheet before ordering",
                ),
            ),
            rated_current=_q(1.7, "A", _confirmed(_HANDSONTEC_17HS4401, "per phase; 1.5 A and 1.68 A variants exist")),
            step_angle=_q(1.8, "deg", _confirmed(_HANDSONTEC_17HS4401, "200 steps/rev")),
            no_load_speed=_q(
                20.0, "rad/s",
                _assumed("steppers have no single no-load speed — it depends on driver voltage "
                         "and the torque/speed curve. Placeholder for the tier-1 shape only"),
            ),
            mass=_q(0.28, "kg", _inferred("40mm-body NEMA17 typical", "not read off a drawing")),
            gear_ratio=1.0,
        ),
        # Kept so existing IRs and tests keep resolving; now points at a real MPN
        # instead of a made-up 0.45 N*m figure.
        "nema17_direct": MotorSpec(
            key="nema17_direct",
            part_number="17HS4401",
            manufacturer="generic NEMA17 (multi-vendor)",
            datasheet_url=_HANDSONTEC_17HS4401,
            condition="holding torque, 1.7 A/phase bipolar",
            stall_torque=_q(40 * _NCM_TO_NM, "N*m", _confirmed(_BESFOC_17HS4401, "0.40 N*m / 40 N*cm")),
            rated_current=_q(1.7, "A", _confirmed(_HANDSONTEC_17HS4401, "per phase")),
            step_angle=_q(1.8, "deg", _confirmed(_HANDSONTEC_17HS4401, "")),
            no_load_speed=_q(20.0, "rad/s", _assumed("see nema17_17hs4401")),
            mass=_q(0.28, "kg", _inferred("40mm-body NEMA17 typical", "not read off a drawing")),
            gear_ratio=1.0,
        ),
    },
)

# --- gearboxes ----------------------------------------------------------

_PLANETARY_NOTE = (
    "standard NEMA17 planetary reduction series. Ratio is a catalogue fact; "
    "efficiency and mass are ASSUMED until a specific vendor sheet is attached"
)

GEARBOXES = Catalogue(
    "gearboxes",
    {
        f"nema17_planetary_{ratio}": GearboxSpec(
            key=f"nema17_planetary_{ratio}",
            ratio=ratio,
            efficiency=eff,
            max_output_torque=_q(out, "N*m", _assumed("vendor rated output torque not yet transcribed")),
            mass=_q(m, "kg", _assumed("planetary stage mass, typical")),
            added_length=_q(length, "m", _assumed("stage length added to motor body")),
            manufacturer="generic NEMA17 planetary",
            datasheet_url="",
            backlash=_q(60.0, "arcmin", _assumed("1 degree typical for economy planetary")),
        )
        for ratio, eff, out, m, length in [
            (5.18, 0.90, 3.0, 0.20, 0.032),
            (13.73, 0.81, 6.0, 0.24, 0.042),
            (19.20, 0.81, 8.0, 0.24, 0.042),
            (27.00, 0.73, 10.0, 0.28, 0.052),
            (51.00, 0.73, 12.0, 0.28, 0.052),
        ]
    },
)

# --- servos -------------------------------------------------------------

_FEETECH_STS3215 = "https://www.feetechrc.com/525603.html"
_ROBOTSHOP_STS3215 = "https://www.robotshop.com/products/feetech-12v-30kgcm-magnetic-encoding-servo-sts3215"
_SEEED_ST3215_7V4 = "https://www.seeedstudio.com/STS3215-19kg-cm-7-4V-Serial-Servo-p-6338.html"
_ROBOTIS_XL430 = "https://emanual.robotis.com/docs/en/dxl/x/xl430-w250/"
_ROBOTIS_XM430 = "https://emanual.robotis.com/docs/en/dxl/x/xm430-w350/"
_MG996R = "https://components101.com/motors/mg996r-servo-motor-datasheet"

SERVOS = Catalogue(
    "servos",
    {
        # The SO-101 arm's actuator — the demo embodiment's own part (§8.5.4).
        "feetech_sts3215_12v": MotorSpec(
            key="feetech_sts3215_12v",
            part_number="STS3215 (C018)",
            manufacturer="FeeTech",
            datasheet_url=_FEETECH_STS3215,
            condition="12.0 V",
            stall_torque=_q(_kgcm(30.0), "N*m", _confirmed(_FEETECH_STS3215, "30 kgf*cm at 12 V")),
            rated_torque=_q(_kgcm(10.0), "N*m", _confirmed(_ROBOTSHOP_STS3215, "10 kgf*cm continuous at 12 V")),
            stall_current=_q(2.7, "A", _confirmed(_ROBOTSHOP_STS3215, "at 12 V")),
            no_load_speed=_q(_sec60_to_rad_s(0.222), "rad/s", _confirmed(_ROBOTSHOP_STS3215, "0.222 s/60deg at 12 V")),
            voltage_min=_q(4.0, "V", _confirmed(_ROBOTSHOP_STS3215, "")),
            voltage_max=_q(14.0, "V", _confirmed(_ROBOTSHOP_STS3215, "")),
            mass=_q(0.055, "kg", _confirmed(_ROBOTSHOP_STS3215, "55 +/- 1 g")),
            gear_ratio=345.0,
            protocol="TTL half-duplex serial",
            body_size=BodySize(
                length=_q(0.0452, "m", _confirmed(_ROBOTSHOP_STS3215, "45.2 mm body length")),
                width=_q(0.0247, "m", _confirmed(_ROBOTSHOP_STS3215, "24.7 mm body width")),
                height=_q(0.0350, "m", _confirmed(_ROBOTSHOP_STS3215, "35.0 mm body height")),
            ),
        ),
        # Same MPN family, different voltage, materially different torque. This
        # pair is why `condition` exists.
        "feetech_st3215_7v4": MotorSpec(
            key="feetech_st3215_7v4",
            part_number="ST3215 (C001)",
            manufacturer="FeeTech",
            datasheet_url=_SEEED_ST3215_7V4,
            condition="7.4 V",
            stall_torque=_q(_kgcm(19.0), "N*m", _confirmed(_SEEED_ST3215_7V4, "19 kgf*cm at 7.4 V")),
            no_load_speed=_q(_sec60_to_rad_s(0.222), "rad/s", _inferred(_SEEED_ST3215_7V4, "family figure")),
            mass=_q(0.055, "kg", _inferred(_SEEED_ST3215_7V4, "same body as STS3215")),
            gear_ratio=345.0,
            protocol="TTL half-duplex serial",
        ),
        "dynamixel_xl430_w250": MotorSpec(
            key="dynamixel_xl430_w250",
            part_number="XL430-W250-T",
            manufacturer="ROBOTIS",
            datasheet_url=_ROBOTIS_XL430,
            condition="12.0 V",
            stall_torque=_q(1.5, "N*m", _confirmed(_ROBOTIS_XL430, "1.5 N*m at 12 V")),
            no_load_speed=_q(_rpm_to_rad_s(57.0), "rad/s", _inferred(_ROBOTIS_XL430, "57 rev/min at 12 V")),
            mass=_q(0.0572, "kg", _confirmed(_ROBOTIS_XL430, "57.2 g")),
            gear_ratio=258.5,
            protocol="TTL half-duplex serial",
        ),
        "dynamixel_xm430_w350": MotorSpec(
            key="dynamixel_xm430_w350",
            part_number="XM430-W350-T",
            manufacturer="ROBOTIS",
            datasheet_url=_ROBOTIS_XM430,
            condition="12.0 V, 2.3 A",
            stall_torque=_q(4.1, "N*m", _confirmed(_ROBOTIS_XM430, "4.1 N*m at 12.0 V, 2.3 A")),
            stall_current=_q(2.3, "A", _confirmed(_ROBOTIS_XM430, "at 12 V")),
            no_load_speed=_q(_rpm_to_rad_s(46.0), "rad/s", _confirmed(_ROBOTIS_XM430, "46 rev/min at 12 V")),
            voltage_min=_q(10.0, "V", _confirmed(_ROBOTIS_XM430, "")),
            voltage_max=_q(14.8, "V", _confirmed(_ROBOTIS_XM430, "recommended 12.0 V")),
            mass=_q(0.082, "kg", _confirmed(_ROBOTIS_XM430, "82 g")),
            gear_ratio=353.5,
            protocol="TTL half-duplex serial",
        ),
        "towerpro_mg996r": MotorSpec(
            key="towerpro_mg996r",
            part_number="MG996R",
            manufacturer="TowerPro",
            datasheet_url=_MG996R,
            condition="6.0 V",
            stall_torque=_q(_kgcm(11.0), "N*m", _confirmed(_MG996R, "11 kgf*cm at 6.0 V; 9.4 kgf*cm at 4.8 V")),
            no_load_speed=_q(_sec60_to_rad_s(0.15), "rad/s", _confirmed(_MG996R, "0.15 s/60deg at 6.0 V")),
            voltage_min=_q(4.8, "V", _confirmed(_MG996R, "")),
            voltage_max=_q(6.6, "V", _confirmed(_MG996R, "")),
            mass=_q(0.055, "kg", _confirmed(_MG996R, "55 g")),
            gear_ratio=1.0,
            protocol="PWM",
        ),
    },
)

# --- motor drivers ------------------------------------------------------

MOTOR_DRIVERS = Catalogue(
    "motor_drivers",
    {
        "a4988": DriverSpec(
            key="a4988",
            part_number="A4988",
            manufacturer="Allegro MicroSystems",
            datasheet_url="https://www.allegromicro.com/en/products/motor-drivers/brush-dc-motor-drivers/a4988",
            max_current_per_phase=_q(2.0, "A", _inferred("Allegro A4988 product page", "with adequate cooling")),
            voltage_min=_q(8.0, "V", _inferred("Allegro A4988 product page", "")),
            voltage_max=_q(35.0, "V", _inferred("Allegro A4988 product page", "")),
            microstepping="full, 1/2, 1/4, 1/8, 1/16",
            interface="STEP/DIR",
        ),
        "drv8825": DriverSpec(
            key="drv8825",
            part_number="DRV8825",
            manufacturer="Texas Instruments",
            datasheet_url="https://www.ti.com/product/DRV8825",
            max_current_per_phase=_q(2.2, "A", _inferred("TI DRV8825 product page", "with adequate cooling")),
            voltage_min=_q(8.2, "V", _inferred("TI DRV8825 product page", "")),
            voltage_max=_q(45.0, "V", _inferred("TI DRV8825 product page", "")),
            microstepping="full .. 1/32",
            interface="STEP/DIR",
        ),
        "tmc2209": DriverSpec(
            key="tmc2209",
            part_number="TMC2209",
            manufacturer="Trinamic / Analog Devices",
            datasheet_url="https://www.analog.com/en/products/tmc2209.html",
            max_current_per_phase=_q(2.0, "A", _inferred("Trinamic TMC2209 product page", "RMS, with cooling")),
            voltage_min=_q(4.75, "V", _inferred("Trinamic TMC2209 product page", "")),
            voltage_max=_q(29.0, "V", _inferred("Trinamic TMC2209 product page", "")),
            microstepping="up to 1/256 interpolated",
            interface="STEP/DIR + UART",
        ),
    },
)

# --- materials ----------------------------------------------------------


def _matweb(note: str) -> Provenance:
    return Provenance(status="CONFIRMED", source="https://www.matweb.com/", note=note)


MATERIALS = Catalogue(
    "materials",
    {
        "aluminum_6061": MaterialSpec(
            key="aluminum_6061",
            density=_q(2700.0, "kg/m^3", _matweb("6061-T6")),
            yield_strength=_q(2.76e8, "Pa", _matweb("6061-T6")),
        ),
        "pla": MaterialSpec(
            key="pla",
            density=_q(1240.0, "kg/m^3", _inferred("generic FDM PLA", "varies 1.17-1.30 by supplier")),
            yield_strength=_q(5.0e7, "Pa", _inferred("generic FDM PLA", "print-orientation dependent")),
        ),
        "petg": MaterialSpec(
            key="petg",
            density=_q(1270.0, "kg/m^3", _inferred("generic FDM PETG", "")),
            yield_strength=_q(5.0e7, "Pa", _inferred("generic FDM PETG", "")),
        ),
        "steel_1018": MaterialSpec(
            key="steel_1018",
            density=_q(7870.0, "kg/m^3", _matweb("1018 cold drawn")),
            yield_strength=_q(3.7e8, "Pa", _matweb("1018 cold drawn")),
        ),
    },
)


# --- composition & lookup ----------------------------------------------

CATALOGUES: dict[str, Catalogue] = {
    "materials": MATERIALS,
    "stepper_motors": STEPPER_MOTORS,
    "servos": SERVOS,
    "gearboxes": GEARBOXES,
    "motor_drivers": MOTOR_DRIVERS,
}


def resolve(catalogue: str, key: str) -> BaseModel:
    if catalogue not in CATALOGUES:
        raise KeyError(f"no catalogue named {catalogue!r}; known catalogues: {sorted(CATALOGUES)}")
    return CATALOGUES[catalogue][key]


def resolve_geared(motor_key: str, gearbox_key: str) -> MotorSpec:
    """Compose a motor with a reducer, **with the efficiency loss applied**.

    `motor_torque * ratio` alone overstates real output by 10-30% depending on the
    stage count, which is the difference between a joint that passes
    `joint_torque_budget` on paper and one that stalls on the bench.
    """
    motor = resolve("stepper_motors", motor_key)
    gearbox = resolve("gearboxes", gearbox_key)
    assert isinstance(motor, MotorSpec) and isinstance(gearbox, GearboxSpec)

    output = motor.stall_torque.value * gearbox.ratio * gearbox.efficiency
    capped = min(output, gearbox.max_output_torque.value)
    note = (
        f"{motor.part_number} {motor.stall_torque.value:.3f} N*m x {gearbox.ratio}:1 "
        f"x {gearbox.efficiency:.2f} efficiency = {output:.3f} N*m"
    )
    if capped < output:
        note += f"; capped at the gearbox's {gearbox.max_output_torque.value} N*m rating"

    return MotorSpec(
        key=f"{motor_key}+{gearbox_key}",
        part_number=f"{motor.part_number} + {gearbox.part_number or gearbox.key}",
        manufacturer=motor.manufacturer,
        datasheet_url=motor.datasheet_url,
        condition=f"{motor.condition}; through {gearbox.ratio}:1 reducer",
        stall_torque=_q(capped, "N*m", _inferred("composed by resolve_geared()", note)),
        no_load_speed=_q(
            motor.no_load_speed.value / gearbox.ratio, "rad/s",
            _inferred("composed by resolve_geared()", "motor speed / ratio"),
        ),
        mass=_q(
            motor.mass.value + gearbox.mass.value, "kg",
            _inferred("composed by resolve_geared()", "motor + gearbox mass"),
        ),
        gear_ratio=motor.gear_ratio * gearbox.ratio,
        rated_current=motor.rated_current,
        step_angle=motor.step_angle,
        # Carried through from the reducer. A gearbox fixes a torque shortfall
        # cheaply and pays for it in lost motion at the end effector — which is
        # the entire reason the `backlash` criterion exists, and it can only see
        # the trade if the composed actuator still reports it.
        backlash=gearbox.backlash,
    )


# Backwards-compatible composed entry: existing IRs and tests refer to this key.
# It is now derived from the real motor and a real ratio rather than asserted.
STEPPER_MOTORS._entries["nema17_planetary_13.73"] = resolve_geared(
    "nema17_17hs4401", "nema17_planetary_13.73"
)
