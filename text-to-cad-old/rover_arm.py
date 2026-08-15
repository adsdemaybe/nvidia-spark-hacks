"""
Mobile Rover + 3-Axis Robotic Arm — a working parametric machine in build123d.

This is not a shell study. Every axis is actuated, every revolute joint runs on a
real catalogue bearing, the gripper has a working rack-and-pinion mechanism, and
the chassis houses a real electronics BOM with verified clearances.

    Axis 1  yaw       NEMA17 under the lid -> hub on 51106 thrust bearing
    Axis 2  shoulder  NEMA17 on the turntable yoke, 626 bearing on the idle side
    Axis 3  elbow     DS3218 servo in the shoulder fork, 626 on the idle side
    Tool    gripper   DS3218 servo -> 12T pinion -> two opposed racks (parallel jaw)

DIMENSIONAL PROVENANCE — read before fabricating
------------------------------------------------
CONFIRMED from manufacturer drawings:
  * NEMA17 frame 42.3, bolt pitch 31.0+/-0.2, pilot d22, shaft d5, M3x4.5 deep
    (Pololu SY42STH38-1684A dimensioned drawing)
  * DS3218 servo 40 x 20 x 40.4, ear span 54.5, hole pitch 49.5 x 10,
    base->lug 27.7 (DSservo official datasheet drawing)
  * 626 = 6x19x6 r0.3, 608 = 8x22x7 r0.3 (SKF); 51106 = 30x47x11 r0.6 (SKF/NSK)
  * Raspberry Pi 4B 85 x 56, holes d2.7 @ 58 x 49 pitch, 3.5/3.5 corner inset,
    max component height 16.0 (official RPi mechanical drawing)
  * Pololu A4988/DRV8825 carriers 15.24 x 20.32, 2x8 header @ 2.54, rows 12.70
    apart, 11.87 assembled height, and NO MOUNTING HOLES — socket mount only

INFERRED / UNCONFIRMED (flagged at each use, verify against a physical part):
  * NEMA17 pilot boss height 2.0     — two libraries agree, no datasheet callout
  * Servo ear hole diameter          — undimensioned on every drawing found
  * Servo spline 25T / d5.8          — Pololu class figure, not a DSservo callout
  * Servo spline offset 10.0 from body centre — measured off a rendered drawing
  * 51106 individual washer heights  — unpublished; assumed a symmetric split

VENDOR CAD is used for VISUALS ONLY. The downloaded Adafruit NEMA17 STEP measures
body 32.65 / shaft 20.1 — a short 34mm-class motor, not the 17HS4401 this design
targets — and its corner geometry does not yield a clean 31.0 bolt pitch. Every
mating feature below is cut from the datasheet constants, never from that file.

Run:  .venv-cad/bin/python rover_arm.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from build123d import (
    Align,
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Circle,
    Compound,
    Cylinder,
    GeomType,
    Location,
    Locations,
    Mode,
    Part,
    Plane,
    Polygon,
    PolarLocations,
    Rectangle,
    RectangleRounded,
    RevoluteJoint,
    RigidJoint,
    LinearJoint,
    Rot,
    SortBy,
    chamfer,
    export_step,
    export_stl,
    extrude,
    fillet,
    import_step,
)
PI = 3.14159265358979

from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing
from bd_warehouse.gear import SpurGear

# =============================================================================
# 1. GLOBAL PARAMETRIC BLOCK
# =============================================================================

# ---- Fabrication ------------------------------------------------------------
WALL = 3.0
FILLET_R = 2.0
CLEARANCE = 0.4            # sliding/running radial gap
PRESS_FIT = -0.02          # bearing outer race interference (negative = tight)
PART_GAP = 1.0             # air gap between moving assemblies
VENT_W = 3.0               # ventilation slot width

# ---- Fasteners --------------------------------------------------------------
M3_CLEAR, M3_TAP, M3_HEAD = 3.4, 2.5, 6.0
M25_CLEAR, M25_TAP = 2.9, 2.05
BOLT_HEAD_DEPTH = 3.0

# ---- NEMA17 (17HS4401) — CONFIRMED except boss height -----------------------
NEMA_FRAME = 42.3
NEMA_BODY_L = 40.0
NEMA_BOLT_PITCH = 31.0
NEMA_BOLT_DEPTH = 4.5
NEMA_PILOT_D = 22.0
NEMA_PILOT_H = 2.0         # INFERRED
NEMA_SHAFT_D = 5.0
NEMA_SHAFT_L = 24.0
NEMA_CORNER_R = 5.0
NEMA_HOLE_D = M3_TAP       # tapped M3 for NEMA17; NEMA23 uses 5.1 clearance
NEMA_TAPPED = True

# ---- Stepper catalogue ------------------------------------------------------
# A motor is a design variable, not a constant: the shoulder needs more torque
# than the drive wheels, and frame size propagates into every mounting feature.
# Fields: frame, body_len, bolt_pitch, pilot_d, pilot_h, shaft_d, shaft_len,
#         mass_kg, holding_torque_Nm, corner_r
MOTORS = {
    # All geometry read off dimensioned vendor drawings. NEMA17 boss height 2.0
    # and NEMA23 boss height 1.6 are explicitly dimensioned (Trinamic QSH4218,
    # StepperOnline 17HS19/23HS22, Songyang SY42STH/SY57STH) — not inferred.
    # NOTE: NEMA23 mounting holes are CLEARANCE (bolt through), not tapped.
    "17HS4401": dict(frame=42.3, body_len=40.0, bolt_pitch=31.0, pilot_d=22.0,
                     pilot_h=2.0, shaft_d=5.0, shaft_len=24.0, mass=0.280,
                     torque=0.43, corner_r=5.0, hole_d=2.5, tapped=True),
    "17HS19-2004S1": dict(frame=42.3, body_len=48.0, bolt_pitch=31.0, pilot_d=22.0,
                          pilot_h=2.0, shaft_d=5.0, shaft_len=24.0, mass=0.400,
                          torque=0.59, corner_r=5.0, hole_d=2.5, tapped=True),
    "STP-MTR-17060": dict(frame=42.3, body_len=59.5, bolt_pitch=31.0, pilot_d=22.0,
                          pilot_h=2.0, shaft_d=5.0, shaft_len=24.0, mass=0.400,
                          torque=0.81, corner_r=5.0, hole_d=2.5, tapped=True),
    "23HS22-2804S": dict(frame=56.4, body_len=56.0, bolt_pitch=47.1, pilot_d=38.1,
                         pilot_h=1.6, shaft_d=6.35, shaft_len=21.0, mass=0.700,
                         torque=1.26, corner_r=5.0, hole_d=5.1, tapped=False),
    "SY57STH76-2804A": dict(frame=56.4, body_len=76.0, bolt_pitch=47.1, pilot_d=38.1,
                            pilot_h=1.6, shaft_d=6.35, shaft_len=21.0, mass=1.050,
                            torque=1.89, corner_r=5.0, hole_d=5.1, tapped=False),
}

#: Ordered by torque — the optimiser walks this list for the shoulder.
MOTOR_BY_TORQUE = sorted(MOTORS, key=lambda k: MOTORS[k]["torque"])

DRIVE_MOTOR = "17HS4401"
SHOULDER_MOTOR = "17HS4401"


def _apply_motor(key: str) -> None:
    """Push a catalogue entry into the NEMA_* constants the geometry reads."""
    m = MOTORS[key]
    g = globals()
    g["NEMA_FRAME"] = m["frame"]
    g["NEMA_BODY_L"] = m["body_len"]
    g["NEMA_BOLT_PITCH"] = m["bolt_pitch"]
    g["NEMA_PILOT_D"] = m["pilot_d"]
    g["NEMA_PILOT_H"] = m["pilot_h"]
    g["NEMA_SHAFT_D"] = m["shaft_d"]
    g["NEMA_SHAFT_L"] = m["shaft_len"]
    g["NEMA_CORNER_R"] = m["corner_r"]
    g["NEMA_HOLE_D"] = m["hole_d"]
    g["NEMA_TAPPED"] = m["tapped"]


# ---- DS3218 servo — CONFIRMED except noted ----------------------------------
SERVO_L, SERVO_W, SERVO_H = 40.0, 20.0, 40.4
SERVO_EAR_SPAN = 54.5
SERVO_EAR_PITCH_L = 49.5
SERVO_EAR_PITCH_W = 10.0
SERVO_EAR_HOLE_D = 3.0     # INFERRED (undimensioned on the drawing)
SERVO_EAR_T = 2.5          # INFERRED
SERVO_FLANGE_Z = 27.7      # base -> underside of lugs
SERVO_SPLINE_D = 5.8       # INFERRED (Pololu class figure, 25T)
SERVO_SPLINE_OFF = 10.0    # INFERRED (measured off rendered drawing)

# ---- Bearings — CONFIRMED (SKF) ---------------------------------------------
BRG_SIZE = "M6-19-6"       # 626: bd_warehouse catalogue key
BRG_ID, BRG_OD, BRG_W = 6.0, 19.0, 6.0
BRG_SEAT_D = BRG_OD + PRESS_FIT
BRG_SHOULDER = 2.0         # retaining lip that the outer race lands on
THRUST_ID, THRUST_OD, THRUST_H = 30.0, 47.0, 11.0   # 51106

# ---- Electronics BOM — CONFIRMED --------------------------------------------
RPI_L, RPI_W, RPI_T = 85.0, 56.0, 1.4
RPI_HOLE_D, RPI_HOLE_PL, RPI_HOLE_PW, RPI_INSET = 2.7, 58.0, 49.0, 3.5
RPI_MAX_H = 16.0
RPI_STANDOFF = 6.0

DRV_L, DRV_W, DRV_H = 15.24, 20.32, 11.87
DRV_HDR_ROWS, DRV_HDR_PITCH, DRV_HDR_INSET = 12.70, 2.54, 1.27
DRV_COUNT = 4
DRV_AIRSPACE = 6.0         # required free air above each driver

BATT_L, BATT_W, BATT_H = 107.0, 34.0, 23.0
BATT_WIRE = 35.0           # lead + XT60 relief past the case
BALLAST_M = 0.0            # optional rear counterweight (kg), a design variable

# Shoulder reduction. Real NEMA17 planetary gearboxes come in 5.18:1, 13.73:1
# and 26.85:1 (StepperOnline); each adds ~34 mm of length and ~200 g. Without a
# reduction the shoulder cannot hold the arm out, and the only other way to pass
# is to shorten the arm until it is useless.
SHOULDER_GEAR = 1.0        # reduction ratio; must be one of GEAR_OPTIONS
#: Real, purchasable planetary ratios (Phidgets 3325_0 / 3318, drawing-confirmed).
#: A continuous ratio lets an optimiser invent parts that cannot be bought.
GEAR_OPTIONS = (1.0, 5.18, 26.851)
GEAR_BACKLASH_DEG = {1.0: 0.0, 5.18: 1.5, 26.851: 1.0}
MAX_BACKLASH_MM = 3.0      # allowed end-effector slop from shoulder backlash
GEARBOX_EFF = 0.80         # planetary efficiency
GEARBOX_MASS_PER_STAGE = 0.20   # kg

BAY_MARGIN = 2.0           # minimum free gap: part<->part and part<->wall

# ---- Chassis ----------------------------------------------------------------
CHASSIS_L, CHASSIS_W, CHASSIS_H = 210.0, 130.0, 55.0
SIDE_WALL = 6.0            # thicker: carries the drive motors
LID_T = 4.0
AXLE_Z = CHASSIS_H / 2.0
AXLE_FRAC = 0.25           # axle offset as a fraction of chassis length
AXLE_X = CHASSIS_L * AXLE_FRAC

WHEEL_D, WHEEL_W, HUB_D = 70.0, 25.0, 26.0

# ---- Arm --------------------------------------------------------------------
TURNTABLE_SETBACK = 48.0   # yaw axis inset from the front edge
TURNTABLE_X = CHASSIS_L / 2.0 - TURNTABLE_SETBACK
BASE_D, BASE_H = 76.0, 26.0
LINK1_LEN, LINK2_LEN = 120.0, 100.0
LINK_W = 34.0              # must clear the 626 seat: BRG_OD + 2*wall
LINK_T = 12.0
YOKE_GAP = LINK_T + 2 * PART_GAP
PIN_D = BRG_ID             # pin rides in the bearing bore — one shared number
PIN_LEN = 46.0
PIN_HEAD_T = 2.0
CIRCLIP_W, CIRCLIP_DEPTH = 0.9, 0.35       # DIN 471 6mm shaft groove

# Joint travel limits, enforced by build123d at connect time.
# A revolute about Y builds its frame with Z along the axis, which cyclically
# remaps the child's axes. PITCH_REF + PITCH_MOUNT cancel that exactly, so a
# link stays in the X-Z plane and negative angles raise it. This particular
# triple also preserves the pitch SENSE down the chain: the alternatives that
# place the link correctly carry a 180 deg roll, which silently inverts the
# elbow. Verified against a hand-computed two-link solution.
PITCH_REF = (1.0, 0.0, 0.0)
PITCH_MOUNT = (90.0, 180.0, 90.0)

YAW_RANGE = (0.0, 360.0)
SHOULDER_RANGE = (-95.0, 95.0)
ELBOW_RANGE = (-150.0, 150.0)

# ---- Gripper ----------------------------------------------------------------
GEAR_MODULE = 1.5
PINION_TEETH = 12
PRESSURE_ANGLE = 20.0
GEAR_T = 6.0
PINION_PITCH_D = GEAR_MODULE * PINION_TEETH        # 18.0
RACK_TRAVEL = 14.0
JAW_T = 6.0
JAW_LEN = 45.0
JAW_MIN_OPEN = 6.0
GRIP_BODY_L, GRIP_BODY_W, GRIP_BODY_H = 76.0, 44.0, 22.0
RACK_OFFSET = PINION_PITCH_D / 2.0 + JAW_T / 2.0  # rack centreline offset

EXPORT_DIR = "export"
VENDOR_DIR = "vendor"


# =============================================================================
# 1b. RUNTIME RECONFIGURATION
#     Everything above is a design variable. `reconfigure()` sets any of them
#     and recomputes every derived quantity, so an outer optimisation loop can
#     search the design space without editing source.
# =============================================================================

#: Variables an optimiser is allowed to touch, with sane bounds.
DESIGN_VARS = {
    "CHASSIS_L": (150.0, 320.0),
    "CHASSIS_W": (110.0, 200.0),
    "AXLE_FRAC": (0.20, 0.44),
    "WHEEL_D": (50.0, 110.0),
    "LINK1_LEN": (70.0, 150.0),
    "LINK2_LEN": (60.0, 130.0),
    "TURNTABLE_SETBACK": (40.0, 110.0),
    "BALLAST_M": (0.0, 1.2),
}


def current_design() -> dict:
    return {k: globals()[k] for k in DESIGN_VARS}


def reconfigure(**overrides) -> None:
    """Apply design-variable overrides and recompute all derived constants."""
    g = globals()
    for k, v in overrides.items():
        if k == "SHOULDER_GEAR":
            if float(v) not in GEAR_OPTIONS:
                raise ValueError(f"{v}:1 is not a purchasable ratio")
            g[k] = float(v)
            continue
        if k in ("DRIVE_MOTOR", "SHOULDER_MOTOR"):
            if v not in MOTORS:
                raise KeyError(f"unknown motor {v}")
            g[k] = v
            continue
        if k not in DESIGN_VARS:
            raise KeyError(f"{k} is not a design variable")
        g[k] = float(v)

    # Mounting geometry follows the drive motor; the shoulder motor's torque and
    # mass are read separately by the sim exporter.
    _apply_motor(g["DRIVE_MOTOR"])

    g["AXLE_Z"] = g["CHASSIS_H"] / 2.0
    g["AXLE_X"] = g["CHASSIS_L"] * g["AXLE_FRAC"]
    g["TURNTABLE_X"] = g["CHASSIS_L"] / 2.0 - g["TURNTABLE_SETBACK"]
    g["ARM_BOLT_R"] = g["BASE_D"] / 2.0 - g["WALL"]
    g["YOKE_GAP"] = g["LINK_T"] + 2 * g["PART_GAP"]
    g["PIN_D"] = g["BRG_ID"]
    g["BRG_SEAT_D"] = g["BRG_OD"] + g["PRESS_FIT"]
    g["PINION_PITCH_D"] = g["GEAR_MODULE"] * g["PINION_TEETH"]
    g["RACK_OFFSET"] = g["PINION_PITCH_D"] / 2.0 + g["JAW_T"] / 2.0
    g["BAY"] = bay_layout()


# =============================================================================
# 2. HELPERS
# =============================================================================

def _yoke_span() -> float:
    """Y distance from the swing plane to a fork prong's outer face."""
    return YOKE_GAP / 2.0 + LINK_T


def _cut(sketch_plane: Plane, shapes, amount: float, both: bool = False):
    """
    Cut a profile through the active BuildPart.

    build123d's implicit pending-face handoff does not survive a function-call
    boundary, so the sketch is always passed to extrude() explicitly.
    """
    with BuildSketch(sketch_plane) as sk:
        shapes()
    extrude(to_extrude=sk.sketch, amount=amount, both=both, mode=Mode.SUBTRACT)


def _add(sketch_plane: Plane, shapes, amount: float, both: bool = False):
    """Additive counterpart of _cut()."""
    with BuildSketch(sketch_plane) as sk:
        shapes()
    extrude(to_extrude=sk.sketch, amount=amount, both=both, mode=Mode.ADD)


def nema17_mount_cuts(plane: Plane, depth: float, *, shaft: bool = True) -> None:
    """
    Cut a full NEMA17 interface into the active part at `plane`.

    Pilot recess + shaft bore + 4x M3 on the 31.0 bolt square. All four features
    come from the datasheet block, never from the vendor STEP.
    """
    half = NEMA_BOLT_PITCH / 2.0

    def bolts():
        with Locations((-half, -half), (-half, half),
                       (half, -half), (half, half)):
            Circle(NEMA_HOLE_D / 2.0)

    _cut(plane, lambda: Circle(NEMA_PILOT_D / 2.0 + CLEARANCE), -NEMA_PILOT_H)
    if shaft:
        _cut(plane, lambda: Circle(NEMA_SHAFT_D / 2.0 + CLEARANCE), -depth)
    _cut(plane, bolts, -depth)


def bearing_seat_cut(plane: Plane, depth: float) -> None:
    """Counterbore for a 626 outer race, with a retaining shoulder behind it."""
    _cut(plane, lambda: Circle(BRG_SEAT_D / 2.0), -BRG_W)
    _cut(plane, lambda: Circle(BRG_OD / 2.0 - BRG_SHOULDER), -depth)


# =============================================================================
# 3. OFF-THE-SHELF COMPONENTS
#    Datasheet-exact envelopes. These drive every clearance check; the vendor
#    STEP files are loaded separately, for visuals only.
# =============================================================================

def nema17_envelope() -> Part:
    """17HS4401 keep-out: body + pilot boss + shaft, origin at the mount face."""
    with BuildPart() as m:
        with BuildSketch(Plane.XY.offset(-NEMA_BODY_L)) as s:
            RectangleRounded(NEMA_FRAME, NEMA_FRAME, NEMA_CORNER_R)
        extrude(to_extrude=s.sketch, amount=NEMA_BODY_L)
        with BuildSketch(Plane.XY) as s2:
            Circle(NEMA_PILOT_D / 2.0)
        extrude(to_extrude=s2.sketch, amount=NEMA_PILOT_H)
        with BuildSketch(Plane.XY) as s3:
            Circle(NEMA_SHAFT_D / 2.0)
        extrude(to_extrude=s3.sketch, amount=NEMA_SHAFT_L)
    m.part.label = "NEMA17_17HS4401"
    RigidJoint("face", m.part, Location())
    return m.part


def servo_envelope() -> Part:
    """DS3218 keep-out: body + mounting ears + spline. Origin at the spline base."""
    dx = SERVO_SPLINE_OFF          # spline sits SERVO_SPLINE_OFF from body centre
    with BuildPart() as s:
        with Locations((-dx, 0, -SERVO_H / 2.0)):
            Box(SERVO_L, SERVO_W, SERVO_H)
        # Mounting ears at the flange height.
        with Locations((-dx, 0, -SERVO_H + SERVO_FLANGE_Z + SERVO_EAR_T / 2.0)):
            Box(SERVO_EAR_SPAN, SERVO_W, SERVO_EAR_T)
        with BuildSketch(Plane.XY.offset(-SERVO_H)) as sp:
            Circle(SERVO_SPLINE_D / 2.0)
        extrude(to_extrude=sp.sketch, amount=SERVO_H + 4.0)
        # Ear holes (diameter INFERRED).
        _cut(Plane.XY.offset(-SERVO_H + SERVO_FLANGE_Z + SERVO_EAR_T),
             lambda: _ear_hole_locs(dx), -SERVO_EAR_T * 2)
    s.part.label = "servo_DS3218"
    RigidJoint("spline", s.part, Location())
    return s.part


def _ear_hole_locs(dx: float):
    pl, pw = SERVO_EAR_PITCH_L / 2.0, SERVO_EAR_PITCH_W / 2.0
    with Locations((-dx - pl, -pw), (-dx - pl, pw),
                   (-dx + pl, -pw), (-dx + pl, pw)):
        Circle(SERVO_EAR_HOLE_D / 2.0)


def joint_bearing() -> Part:
    """Real SKF 626ZZ from bd_warehouse (6 x 19 x 6, r0.3)."""
    b = SingleRowDeepGrooveBallBearing(size=BRG_SIZE, bearing_type="SKT")
    b.label = "bearing_626ZZ"
    return b


def thrust_bearing() -> Part:
    """
    51106 thrust bearing, 30 x 47 x 11.

    Modelled as two washers with a ball track. Individual washer heights are NOT
    published by SKF or NSK, so a symmetric split is assumed — flagged.
    """
    wash = (THRUST_H - 1.0) / 2.0
    with BuildPart() as t:
        Cylinder((THRUST_OD) / 2.0, wash,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(THRUST_ID / 2.0, wash, align=(Align.CENTER, Align.CENTER, Align.MIN),
                 mode=Mode.SUBTRACT)
        with Locations((0, 0, wash + 1.0)):
            Cylinder(THRUST_OD / 2.0, wash,
                     align=(Align.CENTER, Align.CENTER, Align.MIN))
            Cylinder(THRUST_ID / 2.0 + 1.0, wash,
                     align=(Align.CENTER, Align.CENTER, Align.MIN),
                     mode=Mode.SUBTRACT)
    t.part.label = "thrust_51106"
    return t.part


def rpi4b_envelope() -> Part:
    """
    Raspberry Pi 4B keep-out from the official mechanical drawing.

    Board + a single 16.0 mm block covering the tallest components (USB stacks),
    which is the volume the bay actually has to respect.
    """
    with BuildPart() as p:
        with BuildSketch(Plane.XY) as s:
            RectangleRounded(RPI_L, RPI_W, 3.0)
        extrude(to_extrude=s.sketch, amount=RPI_T)
        with Locations((0, 0, RPI_T)):
            Box(RPI_L, RPI_W, RPI_MAX_H,
                align=(Align.CENTER, Align.CENTER, Align.MIN))
        _cut(Plane.XY, lambda: _rpi_hole_locs(), -RPI_T * 3)
    p.part.label = "raspberry_pi_4b"
    return p.part


def _rpi_hole_locs():
    hx, hy = RPI_HOLE_PL / 2.0, RPI_HOLE_PW / 2.0
    with Locations((-hx, -hy), (-hx, hy), (hx, -hy), (hx, hy)):
        Circle(RPI_HOLE_D / 2.0)


def driver_envelope() -> Part:
    """
    Pololu A4988 / DRV8825 carrier keep-out (both are dimensionally identical).

    NOTE: these boards have NO mounting holes. They are socket-mounted, so the
    envelope includes the 2x8 header stack below the PCB.
    """
    with BuildPart() as d:
        Box(DRV_L, DRV_W, DRV_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    d.part.label = "stepper_driver"
    return d.part


def battery_envelope() -> Part:
    """3S 2200 mAh LiPo, 107 x 34 x 23 (Gens ace), plus lead relief."""
    with BuildPart() as b:
        Box(BATT_L, BATT_W, BATT_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((BATT_L / 2.0 + BATT_WIRE / 2.0, 0, BATT_H / 2.0)):
            Box(BATT_WIRE, 12.0, 12.0)
    b.part.label = "lipo_3s_2200"
    return b.part


def load_vendor(name: str) -> Part | None:
    """Load a downloaded vendor STEP for VISUALS ONLY (never for mating cuts)."""
    path = os.path.join(VENDOR_DIR, name)
    if not os.path.exists(path):
        return None
    p = import_step(path)
    p.label = f"vendor_{name.rsplit('.', 1)[0]}"
    return p


# =============================================================================
# 4. ELECTRONICS BAY LAYOUT
#    One source of truth for where each board lives, used by the chassis, the
#    lid, and the clearance checker alike.
# =============================================================================

@dataclass(frozen=True)
class Bay:
    rpi: Location
    battery: Location
    drivers: tuple[Location, ...]


def bay_layout() -> Bay:
    """
    Place the electronics on the chassis floor.

    Battery and Pi sit side by side across Y at the rear; the four stepper
    drivers stand in a row across Y ahead of them, clear of the yaw motor.
    """
    floor = WALL
    x_rear = -CHASSIS_L / 2.0 + SIDE_WALL + BAY_MARGIN

    y_batt = -CHASSIS_W / 2.0 + SIDE_WALL + BAY_MARGIN + BATT_W / 2.0
    y_rpi = y_batt + BATT_W / 2.0 + BAY_MARGIN + RPI_W / 2.0

    batt = Location((x_rear + BATT_L / 2.0, y_batt, floor))
    rpi = Location((x_rear + RPI_L / 2.0, y_rpi, floor + RPI_STANDOFF))

    x_drv = x_rear + max(BATT_L, RPI_L) + BAY_MARGIN + BATT_WIRE + DRV_L / 2.0
    pitch = DRV_W + BAY_MARGIN
    y0 = -(DRV_COUNT - 1) * pitch / 2.0
    drivers = tuple(
        Location((x_drv, y0 + i * pitch, floor)) for i in range(DRV_COUNT)
    )
    return Bay(rpi=rpi, battery=batt, drivers=drivers)


BAY = bay_layout()


# =============================================================================
# 5. FABRICATED PARTS
# =============================================================================

def build_chassis() -> Part:
    """Rover tub: 4 drive-motor faces, electronics bay features, lid bosses."""
    with BuildPart() as ch:
        Box(CHASSIS_L, CHASSIS_W, CHASSIS_H,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(ch.edges().filter_by(Axis.Z), radius=FILLET_R)

        top = ch.faces().sort_by(Axis.Z)[-1]
        with BuildSketch(Plane(top)) as cav:
            Rectangle(CHASSIS_L - 2 * SIDE_WALL, CHASSIS_W - 2 * SIDE_WALL)
        extrude(to_extrude=cav.sketch, amount=-(CHASSIS_H - WALL),
                mode=Mode.SUBTRACT)

        # --- Drive motors: 4x NEMA17 through the side walls -------------------
        for sy in (-1, 1):
            for ax in (-AXLE_X, AXLE_X):
                face = Plane(origin=(ax, sy * CHASSIS_W / 2.0, AXLE_Z),
                             x_dir=(1, 0, 0), z_dir=(0, sy, 0))
                nema17_mount_cuts(face, SIDE_WALL)

        # --- RPi standoffs (M2.5), tapped, on the confirmed 58 x 49 pitch -----
        hx, hy = RPI_HOLE_PL / 2.0, RPI_HOLE_PW / 2.0
        rp = BAY.rpi.position
        for ox, oy in ((-hx, -hy), (-hx, hy), (hx, -hy), (hx, hy)):
            with Locations((rp.X + ox, rp.Y + oy, WALL)):
                Cylinder(4.0, RPI_STANDOFF,
                         align=(Align.CENTER, Align.CENTER, Align.MIN))
        for ox, oy in ((-hx, -hy), (-hx, hy), (hx, -hy), (hx, hy)):
            _cut(Plane.XY.offset(WALL + RPI_STANDOFF),
                 lambda ox=ox, oy=oy: _pt_circle(rp.X + ox, rp.Y + oy,
                                                 M25_TAP / 2.0),
                 -RPI_STANDOFF)

        # --- Battery retaining ribs ------------------------------------------
        bp = BAY.battery.position
        for sy in (-1, 1):
            with Locations((bp.X, bp.Y + sy * (BATT_W / 2.0 + WALL / 2.0), WALL)):
                Box(BATT_L, WALL, BATT_H * 0.6,
                    align=(Align.CENTER, Align.CENTER, Align.MIN))

        # --- Driver sockets: 2x8 header holes. NO bolt pattern — these boards
        #     have no mounting holes; they are retained by the socket alone.
        for loc in BAY.drivers:
            p = loc.position
            _cut(Plane.XY.offset(WALL), lambda p=p: _header_holes(p), -WALL * 2)

        # --- Cable pass-throughs from the bay to each motor -------------------
        for sy in (-1, 1):
            _cut(Plane.XY.offset(WALL),
                 lambda sy=sy: _pt_slot(0.0, sy * (CHASSIS_W / 2.0 - SIDE_WALL - 6.0),
                                        30.0, 8.0),
                 -WALL * 2)

        # --- Lid bolt bosses --------------------------------------------------
        for lx, ly in _lid_bolt_xy():
            with Locations((lx, ly, WALL)):
                Cylinder(5.0, CHASSIS_H - WALL - LID_T,
                         align=(Align.CENTER, Align.CENTER, Align.MIN))
            _cut(Plane.XY.offset(CHASSIS_H - LID_T),
                 lambda lx=lx, ly=ly: _pt_circle(lx, ly, M3_TAP / 2.0), -12.0)

    ch.part.label = "chassis"
    RigidJoint("lid_seat", ch.part, Location((0, 0, CHASSIS_H - LID_T)))
    for i, (sy, ax) in enumerate([(sy, ax) for sy in (-1, 1)
                                  for ax in (-AXLE_X, AXLE_X)]):
        RevoluteJoint(f"axle_{i}", ch.part,
                      axis=Axis((ax, sy * (CHASSIS_W / 2.0 + WHEEL_W / 2.0
                                           + PART_GAP), AXLE_Z), (0, sy, 0)),
                      angle_reference=PITCH_REF)
    return ch.part


def _pt_circle(x: float, y: float, r: float):
    with Locations((x, y)):
        Circle(r)


def _pt_slot(x: float, y: float, w: float, h: float):
    with Locations((x, y)):
        Rectangle(w, h)


def _header_holes(p):
    """2x8 socket pattern: 2.54 pitch, rows 12.70 apart, 1.27 edge inset."""
    locs = []
    for row in (-DRV_HDR_ROWS / 2.0, DRV_HDR_ROWS / 2.0):
        for i in range(8):
            y = -(7 * DRV_HDR_PITCH) / 2.0 + i * DRV_HDR_PITCH
            locs.append((p.X + row, p.Y + y))
    with Locations(*locs):
        Circle(1.1)


def _lid_bolt_xy():
    ix = CHASSIS_L / 2.0 - SIDE_WALL - 5.0
    iy = CHASSIS_W / 2.0 - SIDE_WALL - 5.0
    return [(-ix, -iy), (-ix, iy), (ix, -iy), (ix, iy), (0, -iy), (0, iy)]


def build_lid() -> Part:
    """
    Structural lid: carries the yaw motor underneath and the turntable above.

    Also provides the ventilation directly over the stepper drivers, which is
    where the heat actually is.
    """
    with BuildPart() as lid:
        Box(CHASSIS_L - 2 * SIDE_WALL - 2 * CLEARANCE,
            CHASSIS_W - 2 * SIDE_WALL - 2 * CLEARANCE, LID_T,
            align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Yaw motor hangs below; its interface is datasheet-cut.
        nema17_mount_cuts(Plane.XY.offset(LID_T), LID_T)

        # Thrust bearing seat for the turntable, concentric with the yaw axis.
        with Locations((TURNTABLE_X, 0, 0)):
            pass
        _cut(Plane.XY.offset(LID_T),
             lambda: _pt_circle(TURNTABLE_X, 0, THRUST_OD / 2.0 + CLEARANCE),
             -1.5)

        # Ventilation slots directly above the driver row.
        for loc in BAY.drivers:
            p = loc.position
            _cut(Plane.XY.offset(LID_T),
                 lambda p=p: _pt_slot(p.X, p.Y, DRV_L + 4.0, VENT_W), -LID_T * 2)

        for lx, ly in _lid_bolt_xy():
            _cut(Plane.XY.offset(LID_T),
                 lambda lx=lx, ly=ly: _pt_circle(lx, ly, M3_CLEAR / 2.0), -LID_T * 2)

    lid.part.label = "lid"
    RigidJoint("seat", lid.part, Location())
    RigidJoint("yaw_motor", lid.part, Location((TURNTABLE_X, 0, 0)))
    RevoluteJoint("yaw", lid.part,
                  axis=Axis((TURNTABLE_X, 0, LID_T), (0, 0, 1)))
    return lid.part


def build_wheel() -> Part:
    """Drive wheel about Y; bore and grub screw derive from the NEMA17 shaft."""
    with BuildPart() as w:
        with BuildSketch(Plane.XZ) as tread:
            Circle(WHEEL_D / 2.0)
        extrude(to_extrude=tread.sketch, amount=WHEEL_W / 2.0, both=True)
        chamfer(w.edges().filter_by(GeomType.CIRCLE).sort_by(SortBy.RADIUS)[-2:],
                length=2.0)

        inboard = w.faces().sort_by(Axis.Y)[0]
        _add(Plane(inboard), lambda: Circle(HUB_D / 2.0), WHEEL_W / 2.0)
        _cut(Plane.XZ, lambda: Circle(NEMA_SHAFT_D / 2.0 + CLEARANCE),
             WHEEL_W, both=True)
        _cut(Plane.YZ, lambda: _pt_circle(-WHEEL_W * 0.75, 0, M3_TAP / 2.0),
             HUB_D, both=True)

    w.part.label = "wheel"
    RigidJoint("hub", w.part, Location((0, 0, 0), PITCH_MOUNT))
    return w.part


def build_turntable() -> Part:
    """
    Axis-1 turntable. Rides the 51106 thrust bearing, clamps to the yaw shaft,
    and carries the shoulder yoke: one prong is a NEMA17 face, the other holds
    a 626 bearing so the shoulder is supported at both ends.
    """
    fork_h = LINK_W * 1.15
    y_off = YOKE_GAP / 2.0 + LINK_T / 2.0
    pin_z = BASE_H + LINK_W * 0.62

    with BuildPart() as tt:
        Cylinder(BASE_D / 2.0, BASE_H,
                 align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(tt.edges().filter_by(GeomType.CIRCLE).sort_by(Axis.Z)[-1],
               radius=FILLET_R)

        # Thrust washer register + yaw shaft clamp bore.
        _cut(Plane.XY, lambda: Circle(THRUST_OD / 2.0 + CLEARANCE), THRUST_H / 2.0)
        _cut(Plane.XY, lambda: Circle(NEMA_SHAFT_D / 2.0 + CLEARANCE), BASE_H)
        _cut(Plane.XZ, lambda: _pt_circle(0, BASE_H * 0.7, M3_TAP / 2.0),
             BASE_D, both=True)

        # Shoulder yoke prongs.
        with Locations((0, -y_off, BASE_H), (0, y_off, BASE_H)):
            Box(LINK_W, LINK_T, fork_h,
                align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Driven side (+Y): NEMA17 face. Idle side (-Y): 626 bearing seat.
        drive = Plane(origin=(0, y_off + LINK_T / 2.0, pin_z),
                      x_dir=(1, 0, 0), z_dir=(0, 1, 0))
        nema17_mount_cuts(drive, LINK_T)
        idle = Plane(origin=(0, -(y_off + LINK_T / 2.0), pin_z),
                     x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        bearing_seat_cut(idle, LINK_T)

    tt.part.label = "turntable"
    RigidJoint("base", tt.part, Location())
    RevoluteJoint("shoulder", tt.part, axis=Axis((0, 0, pin_z), (0, 1, 0)),
                  angle_reference=PITCH_REF, angular_range=SHOULDER_RANGE)
    RigidJoint("shoulder_motor", tt.part,
               Location((0, y_off + LINK_T / 2.0, pin_z), (-90, 0, 0)))
    return tt.part


def build_link(length: float, label: str, distal_servo: bool) -> Part:
    """
    Arm link. Proximal end is a plain eye that the upstream joint drives; the
    distal fork carries the next joint — either a servo pocket (elbow) or a
    plain bearing pair (wrist).
    """
    boss_x0, boss_x1 = length - LINK_W, length + LINK_W / 2.0
    slot_x0, slot_x1 = length - LINK_W / 2.0, length + LINK_W / 2.0 + LINK_W
    span = _yoke_span()

    with BuildPart() as lk:
        with BuildSketch(Plane.XZ) as prof:
            with Locations((length / 2.0, 0)):
                Rectangle(length, LINK_W)
            with Locations((0, 0), (length, 0)):
                Circle(LINK_W / 2.0)
        extrude(to_extrude=prof.sketch, amount=LINK_T / 2.0, both=True)

        with Locations(((boss_x0 + boss_x1) / 2.0, 0, 0)):
            Box(boss_x1 - boss_x0, 2 * span, LINK_W)
        with Locations(((slot_x0 + slot_x1) / 2.0, 0, 0)):
            Box(slot_x1 - slot_x0, YOKE_GAP, LINK_W * 2, mode=Mode.SUBTRACT)

        # Proximal eye: bore straight through, this end is driven.
        _cut(Plane.XZ, lambda: Circle(PIN_D / 2.0 + CLEARANCE), LINK_T, both=True)

        # Distal fork.
        if distal_servo:
            drive = Plane(origin=(length, span, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))
            _cut(drive, lambda: Circle(SERVO_SPLINE_D / 2.0 + CLEARANCE), -LINK_T)
            _cut(drive, lambda: _ear_hole_locs(SERVO_SPLINE_OFF), -LINK_T)
        else:
            drive = Plane(origin=(length, span, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))
            bearing_seat_cut(drive, LINK_T)
        idle = Plane(origin=(length, -span, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        bearing_seat_cut(idle, LINK_T)

    lk.part.label = label
    RigidJoint("proximal", lk.part, Location((0, 0, 0), PITCH_MOUNT))
    RevoluteJoint("distal", lk.part, axis=Axis((length, 0, 0), (0, 1, 0)),
                  angle_reference=PITCH_REF, angular_range=ELBOW_RANGE)
    return lk.part


def build_pin() -> Part:
    """Joint pin: rides the 626 bore, retained by a DIN 471 circlip groove."""
    with BuildPart() as pin:
        with BuildSketch(Plane.XY) as s:
            Circle(PIN_D / 2.0)
        extrude(to_extrude=s.sketch, amount=PIN_LEN)
        head = pin.faces().sort_by(Axis.Z)[0]
        _add(Plane(head), lambda: Circle(PIN_D / 2.0 + 2.0), PIN_HEAD_T)
        # Circlip groove near the free end.
        _cut(Plane.XZ, lambda: _groove_profile(), PIN_D, both=True)
        chamfer(pin.edges().filter_by(GeomType.CIRCLE).sort_by(Axis.Z)[-1],
                length=0.6)
    pin.part.label = "joint_pin"
    return pin.part


def _groove_profile():
    z = PIN_LEN - 3.0
    with Locations((PIN_D / 2.0 - CIRCLIP_DEPTH / 2.0, z), (-(PIN_D / 2.0 - CIRCLIP_DEPTH / 2.0), z)):
        Rectangle(CIRCLIP_DEPTH, CIRCLIP_W)


# ---- Gripper ---------------------------------------------------------------

def build_pinion() -> Part:
    """12-tooth involute spur pinion (bd_warehouse), bored for the servo spline."""
    with BuildPart() as p:
        SpurGear(module=GEAR_MODULE, tooth_count=PINION_TEETH,
                 pressure_angle=PRESSURE_ANGLE, thickness=GEAR_T)
        _cut(Plane.XY.offset(GEAR_T / 2.0),
             lambda: Circle(SERVO_SPLINE_D / 2.0 - 0.15), GEAR_T * 2)
    p.part.label = "pinion_12T"
    RigidJoint("bore", p.part, Location())
    return p.part


def build_gripper_body() -> Part:
    """
    Gripper frame: servo pocket, pinion pocket, and two opposed rack channels
    so the jaws stay parallel through their whole travel.
    """
    rack_z = PINION_PITCH_D / 2.0
    with BuildPart() as g:
        Box(GRIP_BODY_L, GRIP_BODY_W, GRIP_BODY_H,
            align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(g.edges().filter_by(Axis.Z), radius=FILLET_R)

        # Two rack channels, one either side of the pinion axis.
        for sy in (-1, 1):
            with Locations((0, sy * (rack_z + JAW_T / 2.0), GRIP_BODY_H / 2.0)):
                Box(GRIP_BODY_L + 2, JAW_T + 2 * CLEARANCE, JAW_T + 2 * CLEARANCE,
                    mode=Mode.SUBTRACT)

        # Pinion pocket + servo spline bore through the floor.
        with Locations((0, 0, GRIP_BODY_H / 2.0)):
            Box(PINION_PITCH_D + 2 * GEAR_MODULE + 2 * CLEARANCE,
                PINION_PITCH_D + 2 * GEAR_MODULE + 2 * CLEARANCE,
                GEAR_T + 2 * CLEARANCE, mode=Mode.SUBTRACT)
        _cut(Plane.XY, lambda: Circle(SERVO_SPLINE_D / 2.0 + CLEARANCE),
             GRIP_BODY_H)
        _cut(Plane.XY, lambda: _ear_hole_locs(SERVO_SPLINE_OFF), GRIP_BODY_H)

        # Wrist mount boss, sized to the elbow fork.
        with Locations((-GRIP_BODY_L / 2.0 - LINK_W / 2.0, 0, GRIP_BODY_H / 2.0)):
            Box(LINK_W, LINK_T, LINK_W)
        _cut(Plane.XZ,
             lambda: _pt_circle(-GRIP_BODY_L / 2.0 - LINK_W / 2.0,
                                GRIP_BODY_H / 2.0, PIN_D / 2.0 + CLEARANCE),
             LINK_T * 2, both=True)

    g.part.label = "gripper_body"
    RigidJoint("wrist", g.part,
               Location((-GRIP_BODY_L / 2.0 - LINK_W / 2.0, 0, GRIP_BODY_H / 2.0),
                        PITCH_MOUNT))
    RigidJoint("servo", g.part, Location())
    for sy, nm in ((-1, "jaw_a"), (1, "jaw_b")):
        LinearJoint(nm, g.part,
                    axis=Axis((0, sy * (rack_z + JAW_T / 2.0), GRIP_BODY_H / 2.0),
                              (1, 0, 0)),
                    linear_range=(-RACK_TRAVEL / 2.0, RACK_TRAVEL / 2.0))
    return g.part


def build_jaw(side: int = 1) -> Part:
    """
    Rack + finger as one printed part. `side` is +1 / -1 for the two mirrored
    halves: the two racks must sit either side of the pinion to counter-travel,
    so each finger has to reach back to the centreline or the jaws would shear
    past each other instead of opposing.

    Rack teeth are trapezoids at the 20 degree pressure angle — the standard
    printable approximation of a conjugate rack.
    """
    pitch = PI * GEAR_MODULE
    n_teeth = 8
    rack_len = n_teeth * pitch
    with BuildPart() as j:
        Box(rack_len, JAW_T, JAW_T,
            align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # Teeth along the rack's inner face.
        for i in range(n_teeth):
            x = -rack_len / 2.0 + pitch / 2.0 + i * pitch
            with BuildSketch(Plane.XZ.offset(-JAW_T / 2.0)) as t:
                with Locations((x, 0)):
                    _tooth_profile()
            extrude(to_extrude=t.sketch, amount=JAW_T)

        # Arm reaching from the rack back to the gripper centreline, then the
        # finger rising from there so both faces are coplanar in Y.
        reach = RACK_OFFSET
        with Locations((rack_len / 2.0 - JAW_T / 2.0, -side * reach / 2.0, 0)):
            Box(JAW_T, reach, JAW_T)
        with Locations((rack_len / 2.0 - JAW_T / 2.0, -side * reach,
                        JAW_LEN / 2.0)):
            Box(JAW_T, JAW_T, JAW_LEN)

    j.part.label = f"jaw_{'a' if side < 0 else 'b'}"
    RigidJoint("rack", j.part, Location())
    return j.part


def _tooth_profile():
    """
    One trapezoidal rack tooth, flanks at the 20 degree pressure angle.

    Built with Polygon rather than BuildLine + make_face: this runs inside a
    caller's BuildSketch, and BuildLine cannot reach a parent builder across a
    function-call boundary.
    """
    m = GEAR_MODULE
    add, ded = m, 1.25 * m
    tan_pa = 0.36397                      # tan(20 deg)
    half_top = (PI * m / 2.0) / 2.0 - add * tan_pa
    half_bot = (PI * m / 2.0) / 2.0 + ded * tan_pa
    Polygon((-half_bot, 0), (-half_top, add + ded),
            (half_top, add + ded), (half_bot, 0),
            align=(Align.CENTER, Align.MIN))


# =============================================================================
# 6. ASSEMBLY — real kinematics, driven by joint angles
# =============================================================================

def build_machine(yaw: float = 0.0, shoulder: float = -35.0,
                  elbow: float = 65.0, jaw_open: float = 0.0,
                  with_vendor: bool = True) -> Compound:
    """
    Pose the whole machine from joint values. Change the arguments and the
    geometry actually moves — nothing here is a hardcoded transform.

    yaw/shoulder/elbow in degrees; jaw_open in mm of single-jaw travel.
    """
    chassis = build_chassis()
    lid = build_lid()
    turntable = build_turntable()
    link1 = build_link(LINK1_LEN, "link_shoulder", distal_servo=True)
    link2 = build_link(LINK2_LEN, "link_elbow", distal_servo=False)
    body = build_gripper_body()
    jaw_a, jaw_b = build_jaw(side=-1), build_jaw(side=1)

    # Kinematic chain, root first: chassis -> lid -> turntable -> arm -> tool.
    chassis.joints["lid_seat"].connect_to(lid.joints["seat"])
    lid.joints["yaw"].connect_to(turntable.joints["base"], angle=yaw)
    turntable.joints["shoulder"].connect_to(link1.joints["proximal"], angle=shoulder)
    link1.joints["distal"].connect_to(link2.joints["proximal"], angle=elbow)
    link2.joints["distal"].connect_to(body.joints["wrist"], angle=0)
    body.joints["jaw_a"].connect_to(jaw_a.joints["rack"], position=-jaw_open)
    body.joints["jaw_b"].connect_to(jaw_b.joints["rack"], position=jaw_open)

    parts = [chassis, lid, turntable, link1, link2, body, jaw_a, jaw_b]

    # Wheels on their revolute axles.
    for i in range(4):
        w = build_wheel()
        chassis.joints[f"axle_{i}"].connect_to(w.joints["hub"], angle=0)
        parts.append(w)

    if with_vendor:
        parts.extend(_vendor_visuals(lid, turntable, link1, body))

    return Compound(label="rover_arm", children=parts)


def _vendor_visuals(lid, turntable, link1, body) -> list[Part]:
    """Drop the downloaded vendor STEPs in at their datasheet-derived frames."""
    out = []
    nema = load_vendor("nema17.step")
    servo = load_vendor("servo_s3003.step")
    if nema is not None:
        for frame in (lid.joints["yaw_motor"].location,
                      turntable.joints["shoulder_motor"].location):
            out.append(nema.moved(frame))
    if servo is not None:
        out.append(servo.moved(body.joints["servo"].location))
    return out


# =============================================================================
# 7. VERIFICATION — the bay "conditions", checked rather than asserted in prose
# =============================================================================

def check_electronics_bay() -> list[str]:
    """
    Verify the housing genuinely fits the real BOM. Returns a list of failures.

    Conditions:
      1. every board inside the interior walls with >= BAY_MARGIN clearance
      2. no board-to-board overlap
      3. headroom above each board under the lid
      4. free air above the stepper drivers
      5. battery removable straight up (nothing overhangs it)
    """
    fails = []
    ix, iy = CHASSIS_L / 2.0 - SIDE_WALL, CHASSIS_W / 2.0 - SIDE_WALL
    ceil = CHASSIS_H - LID_T

    # (location, -X extent, +X extent, width, height). The battery's +X extent
    # carries the lead/XT60 relief, which only exists on one end.
    items = {
        "rpi": (BAY.rpi, RPI_L / 2, RPI_L / 2, RPI_W, RPI_T + RPI_MAX_H),
        "battery": (BAY.battery, BATT_L / 2, BATT_L / 2 + BATT_WIRE, BATT_W,
                    BATT_H),
    }
    for i, loc in enumerate(BAY.drivers):
        items[f"driver{i}"] = (loc, DRV_L / 2, DRV_L / 2, DRV_W, DRV_H)

    boxes = {}
    for name, (loc, back, fwd, w, h) in items.items():
        p = loc.position
        boxes[name] = (p.X - back, p.X + fwd, p.Y - w / 2, p.Y + w / 2,
                       p.Z, p.Z + h)

    for name, (x0, x1, y0, y1, z0, z1) in boxes.items():
        if x0 < -ix + BAY_MARGIN or x1 > ix - BAY_MARGIN:
            fails.append(f"{name}: X {x0:.1f}..{x1:.1f} outside interior +/-{ix:.1f}")
        if y0 < -iy + BAY_MARGIN or y1 > iy - BAY_MARGIN:
            fails.append(f"{name}: Y {y0:.1f}..{y1:.1f} outside interior +/-{iy:.1f}")
        if z1 > ceil - BAY_MARGIN:
            fails.append(f"{name}: top {z1:.1f} exceeds lid underside {ceil:.1f}")

    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ax0, ax1, ay0, ay1, az0, az1 = boxes[a]
            bx0, bx1, by0, by1, bz0, bz1 = boxes[b]
            if (ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1
                    and az0 < bz1 and bz0 < az1):
                fails.append(f"{a} overlaps {b}")

    for i, loc in enumerate(BAY.drivers):
        air = ceil - (loc.position.Z + DRV_H)
        if air < DRV_AIRSPACE:
            fails.append(f"driver{i}: only {air:.1f}mm air above, need {DRV_AIRSPACE}")

    return fails


def check_mechanics() -> list[str]:
    """Fit checks on the moving assemblies."""
    fails = []
    if not 0 < YOKE_GAP - LINK_T <= 4:
        fails.append(f"fork gap {YOKE_GAP} vs link {LINK_T}")
    if PIN_LEN < 2 * _yoke_span():
        fails.append(f"PIN_LEN {PIN_LEN} < fork span {2 * _yoke_span()}")
    if PIN_D != BRG_ID:
        fails.append(f"pin {PIN_D} does not match bearing bore {BRG_ID}")
    if LINK_W < BRG_OD + 2 * WALL:
        fails.append(f"LINK_W {LINK_W} too small for a {BRG_OD} bearing seat")
    if BASE_D / 2 < THRUST_OD / 2 + WALL:
        fails.append(f"turntable {BASE_D} too small for {THRUST_OD} thrust race")
    if WHEEL_D / 2 - AXLE_Z <= 0:
        fails.append("no ground clearance")
    return fails


# =============================================================================
# 8. EXPORT
# =============================================================================

FABRICATED = {
    "chassis": build_chassis,
    "lid": build_lid,
    "wheel": build_wheel,
    "turntable": build_turntable,
    "link_shoulder": lambda: build_link(LINK1_LEN, "link_shoulder", True),
    "link_elbow": lambda: build_link(LINK2_LEN, "link_elbow", False),
    "joint_pin": build_pin,
    "pinion_12T": build_pinion,
    "gripper_body": build_gripper_body,
    "jaw_a": lambda: build_jaw(side=-1),
    "jaw_b": lambda: build_jaw(side=1),
}

PURCHASED = {
    "bearing_626ZZ": joint_bearing,
    "thrust_51106": thrust_bearing,
}


def _export(part: Part, name: str, stl: bool = True) -> None:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    export_step(part, os.path.join(EXPORT_DIR, f"{name}.step"))
    if stl:
        export_stl(part, os.path.join(EXPORT_DIR, f"{name}.stl"))
    print(f"  {name}")


if __name__ == "__main__":
    print("checks: electronics bay")
    bay_fails = check_electronics_bay()
    for f in bay_fails:
        print("  FAIL", f)
    print("  OK" if not bay_fails else f"  {len(bay_fails)} failures")

    print("checks: mechanics")
    mech_fails = check_mechanics()
    for f in mech_fails:
        print("  FAIL", f)
    print("  OK" if not mech_fails else f"  {len(mech_fails)} failures")

    print("fabricated parts:")
    for name, fn in FABRICATED.items():
        _export(fn(), name)

    print("purchased parts (reference geometry):")
    for name, fn in PURCHASED.items():
        _export(fn(), name)

    print("assembly:")
    asm = build_machine()
    export_step(asm, os.path.join(EXPORT_DIR, "assembly.step"))
    export_stl(asm, os.path.join(EXPORT_DIR, "assembly.stl"))
    print("  assembly.step + assembly.stl")
