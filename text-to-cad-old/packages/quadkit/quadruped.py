"""
Parametric 12-DOF quadruped — a topology test for the design harness.

Three joints per leg (abduction / hip / knee), which is the ANYmal-and-Spot
arrangement, not a toy. Built to answer one question: does the skills + harness
architecture transfer to a robot with a different topology, or was it bespoke to
a wheeled rover?

What transfers unchanged:
    the motor catalogue          (imported from roverkit, single source of truth)
    the inertia machinery        (roverkit.export_sim.combine)
    the evaluate/critique/export script contract
    the "criteria decide, the agent proposes" rule

What does NOT transfer:
    every criterion. `settles` and `drives` are wheel concepts. A quadruped
    needs stance reachability, standing torque, and a support polygon — none of
    which the rover harness has, because a rover cannot fall between its wheels.
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "roverkit") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "roverkit"))

from build123d import (
    Align, Axis, Box, BuildPart, BuildSketch, Circle, Compound, Cylinder,
    GeomType, Location, Locations, Mode, Part, Plane, RevoluteJoint, RigidJoint,
    Rot, Sphere, chamfer, export_step, export_stl, extrude, fillet,
)

from rover_arm import MOTORS, MOTOR_BY_TORQUE   # one catalogue for both robots

# =============================================================================
# PARAMETRIC BLOCK
# =============================================================================

WALL = 3.0
FILLET_R = 2.5
CLEARANCE = 0.4
RHO_PLA = 1.24e-6              # kg/mm^3

# ---- Body -------------------------------------------------------------------
BODY_L, BODY_W, BODY_H = 260.0, 130.0, 70.0
HIP_X_FRAC = 0.34              # hip offset along the body as a fraction of L
ABDUCT_OFF = 55.0              # lateral hip offset: abduction axis -> hip pitch

# ---- Leg --------------------------------------------------------------------
UPPER_LEG = 150.0
LOWER_LEG = 150.0
LEG_W, LEG_T = 40.0, 16.0
FOOT_R = 16.0

# ---- Stance -----------------------------------------------------------------
STANCE_H = 210.0               # hip height when standing (mm)
STEP_REACH = 90.0              # worst-case foot offset ahead of the hip

# ---- Actuation --------------------------------------------------------------
KNEE_MOTOR = "23HS22-2804S"
HIP_MOTOR = "23HS22-2804S"
ABDUCT_MOTOR = "17HS19-2004S1"
GEAR = 1.0                     # direct drive; see GEAR_OPTIONS in roverkit
PAYLOAD_M = 0.0                # carried mass on the body (kg)

SUPPORT_MARGIN_MIN = 25.0      # mm from COM to the edge of the support polygon
BELLY_CLEARANCE_MIN = 60.0     # mm from ground to the underside of the body

DESIGN_VARS = {
    "BODY_L": (180.0, 380.0),
    "BODY_W": (90.0, 200.0),
    "BODY_H": (45.0, 110.0),
    "HIP_X_FRAC": (0.22, 0.46),
    "ABDUCT_OFF": (30.0, 95.0),
    "UPPER_LEG": (90.0, 230.0),
    "LOWER_LEG": (90.0, 230.0),
    "LEG_W": (34.0, 95.0),
    "STANCE_H": (110.0, 320.0),
    "PAYLOAD_M": (0.0, 6.0),
}

DISCRETE_VARS = {
    "KNEE_MOTOR": tuple(MOTOR_BY_TORQUE),
    "HIP_MOTOR": tuple(MOTOR_BY_TORQUE),
    "ABDUCT_MOTOR": tuple(MOTOR_BY_TORQUE),
}

LEGS = ("fl", "fr", "rl", "rr")   # front-left, front-right, rear-left, rear-right


def current_design() -> dict:
    g = globals()
    d = {k: g[k] for k in DESIGN_VARS}
    d.update({k: g[k] for k in DISCRETE_VARS})
    return d


def reconfigure(**overrides) -> None:
    """Apply design overrides. Discrete choices are validated against the catalogue."""
    g = globals()
    for k, v in overrides.items():
        if k in DISCRETE_VARS:
            if v not in MOTORS:
                raise KeyError(f"{v!r} is not in the motor catalogue")
            g[k] = v
        elif k in DESIGN_VARS:
            g[k] = float(v)
        else:
            raise KeyError(f"{k} is not a design variable")


# =============================================================================
# KINEMATICS
# =============================================================================

def stance_angles(height: float, foot_x: float = 0.0):
    """
    Solve the 2-link leg for a foot at (foot_x, -height) relative to the hip.

    Returns (hip_pitch, knee_pitch) in radians, or None when the target is
    outside the leg's annulus — which is a real design failure, not an edge case:
    it means the robot physically cannot stand at that height.
    """
    d = math.hypot(foot_x, height)
    lo, hi = abs(UPPER_LEG - LOWER_LEG), UPPER_LEG + LOWER_LEG
    if not (lo + 1e-6 < d < hi - 1e-6):
        return None
    # Interior knee angle by the cosine rule.
    cos_k = (UPPER_LEG ** 2 + LOWER_LEG ** 2 - d ** 2) / (2 * UPPER_LEG * LOWER_LEG)
    cos_k = max(-1.0, min(1.0, cos_k))
    knee_interior = math.acos(cos_k)
    knee = knee_interior - math.pi                    # 0 = straight leg
    cos_a = (UPPER_LEG ** 2 + d ** 2 - LOWER_LEG ** 2) / (2 * UPPER_LEG * d)
    cos_a = max(-1.0, min(1.0, cos_a))
    alpha = math.acos(cos_a)
    theta_d = math.atan2(foot_x, height)
    hip = theta_d + alpha
    return hip, knee


def knee_position(hip_pitch: float):
    """Knee location in the sagittal plane, hip at the origin, +z down."""
    return (UPPER_LEG * math.sin(hip_pitch), UPPER_LEG * math.cos(hip_pitch))


def hip_positions() -> list[tuple[float, float]]:
    hx, hy = BODY_L * HIP_X_FRAC, BODY_W / 2.0
    return [(hx, hy), (hx, -hy), (-hx, hy), (-hx, -hy)]


# =============================================================================
# PARTS
# =============================================================================

def build_body() -> Part:
    """Torso: shelled box with four hip motor faces on the side walls."""
    with BuildPart() as b:
        Box(BODY_L, BODY_W, BODY_H, align=(Align.CENTER,) * 3)
        fillet(b.edges().filter_by(Axis.X), radius=FILLET_R)
        top = b.faces().sort_by(Axis.Z)[-1]
        with BuildSketch(Plane(top)) as sk:
            from build123d import Rectangle
            Rectangle(BODY_L - 2 * WALL, BODY_W - 2 * WALL)
        extrude(to_extrude=sk.sketch, amount=-(BODY_H - WALL), mode=Mode.SUBTRACT)

        m = MOTORS[ABDUCT_MOTOR]
        half = m["bolt_pitch"] / 2.0
        for hx, hy in hip_positions():
            sign = 1.0 if hy > 0 else -1.0
            plane = Plane(origin=(hx, sign * BODY_W / 2.0, 0),
                          x_dir=(1, 0, 0), z_dir=(0, sign, 0))
            with BuildSketch(plane) as s1:
                Circle(m["pilot_d"] / 2.0 + CLEARANCE)
            extrude(to_extrude=s1.sketch, amount=-m["pilot_h"], mode=Mode.SUBTRACT)
            with BuildSketch(plane) as s2:
                Circle(m["shaft_d"] / 2.0 + CLEARANCE)
            extrude(to_extrude=s2.sketch, amount=-WALL * 2, mode=Mode.SUBTRACT)
            with BuildSketch(plane) as s3:
                with Locations((-half, -half), (-half, half),
                               (half, -half), (half, half)):
                    Circle(m["hole_d"] / 2.0)
            extrude(to_extrude=s3.sketch, amount=-WALL * 2, mode=Mode.SUBTRACT)

    b.part.label = "body"
    return b.part


def build_hip_bracket() -> Part:
    """Abduction link: carries the hip-pitch axis ABDUCT_OFF outboard."""
    with BuildPart() as h:
        Box(LEG_W, ABDUCT_OFF, LEG_W,
            align=(Align.CENTER, Align.MIN, Align.CENTER))
        fillet(h.edges().filter_by(Axis.Y), radius=FILLET_R)
        with BuildSketch(Plane.XZ) as s:
            Circle(MOTORS[HIP_MOTOR]["shaft_d"] / 2.0 + CLEARANCE)
        extrude(to_extrude=s.sketch, amount=ABDUCT_OFF * 2, mode=Mode.SUBTRACT)
    h.part.label = "hip_bracket"
    return h.part


def build_leg_segment(length: float, label: str) -> Part:
    """Upper or lower leg: a tapered bar from joint to joint."""
    with BuildPart() as s:
        with BuildSketch(Plane.XZ) as prof:
            from build123d import Rectangle
            with Locations((0, -length / 2.0)):
                Rectangle(LEG_W * 0.8, length)
            with Locations((0, 0), (0, -length)):
                Circle(LEG_W / 2.0)
        extrude(to_extrude=prof.sketch, amount=LEG_T / 2.0, both=True)
        with BuildSketch(Plane.XZ) as b1:
            Circle(6.0 / 2.0 + CLEARANCE)
        extrude(to_extrude=b1.sketch, amount=LEG_T, both=True, mode=Mode.SUBTRACT)
        with BuildSketch(Plane.XZ.offset(0)) as b2:
            with Locations((0, -length)):
                Circle(6.0 / 2.0 + CLEARANCE)
        extrude(to_extrude=b2.sketch, amount=LEG_T, both=True, mode=Mode.SUBTRACT)
    s.part.label = label
    return s.part


def build_foot() -> Part:
    with BuildPart() as f:
        Sphere(FOOT_R)
        Box(FOOT_R * 3, FOOT_R * 3, FOOT_R,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    f.part.label = "foot"
    return f.part


def build_machine(height: float | None = None, foot_x: float = 0.0) -> Compound:
    """Pose the whole quadruped at a standing height. Returns a Compound."""
    h = height if height is not None else STANCE_H
    sol = stance_angles(h, foot_x)
    if sol is None:
        sol = (0.6, -1.2)                      # unreachable: show a folded pose
    hip_p, knee_p = sol

    parts = [build_body()]
    for (hx, hy), name in zip(hip_positions(), LEGS):
        side = 1.0 if hy > 0 else -1.0
        hip_loc = Location((hx, hy, 0))
        br = build_hip_bracket()
        br.label = f"hip_{name}"
        parts.append(br.locate(hip_loc * Rot(0, 0, 0 if side > 0 else 180)))

        pitch_origin = Location((hx, hy + side * ABDUCT_OFF, 0))
        up = build_leg_segment(UPPER_LEG, f"upper_{name}")
        parts.append(up.locate(pitch_origin * Rot(0, math.degrees(hip_p), 0)))

        kx, kz = knee_position(hip_p)
        knee_loc = pitch_origin * Location((kx, 0, -kz))
        lo = build_leg_segment(LOWER_LEG, f"lower_{name}")
        parts.append(lo.locate(knee_loc * Rot(0, math.degrees(hip_p + knee_p), 0)))

        fx = kx + LOWER_LEG * math.sin(hip_p + knee_p)
        fz = kz + LOWER_LEG * math.cos(hip_p + knee_p)
        ft = build_foot()
        ft.label = f"foot_{name}"
        parts.append(ft.locate(pitch_origin * Location((fx, 0, -fz))))

    return Compound(label="quadruped", children=parts)


# =============================================================================
# DESIGN CHECKS
# =============================================================================

def total_mass() -> float:
    """Printed mass plus the four legs' motors plus any payload."""
    printed = 0.0
    for p in (build_body(), build_hip_bracket(),
              build_leg_segment(UPPER_LEG, "u"), build_leg_segment(LOWER_LEG, "l"),
              build_foot()):
        printed += p.volume * RHO_PLA
    # body once, then bracket/upper/lower/foot four times each
    body = build_body().volume * RHO_PLA
    leg = sum(p.volume * RHO_PLA for p in (
        build_hip_bracket(), build_leg_segment(UPPER_LEG, "u"),
        build_leg_segment(LOWER_LEG, "l"), build_foot()))
    motors = 4 * (MOTORS[KNEE_MOTOR]["mass"] + MOTORS[HIP_MOTOR]["mass"]
                  + MOTORS[ABDUCT_MOTOR]["mass"])
    return body + 4 * leg + motors + PAYLOAD_M


def check_reach() -> list[str]:
    """The legs must actually reach the commanded stance."""
    fails = []
    if stance_angles(STANCE_H, 0.0) is None:
        lo, hi = abs(UPPER_LEG - LOWER_LEG), UPPER_LEG + LOWER_LEG
        fails.append(f"stance height {STANCE_H:.0f} outside the leg's reachable "
                     f"annulus [{lo:.0f}, {hi:.0f}] — the robot cannot stand")
    if stance_angles(STANCE_H, STEP_REACH) is None:
        fails.append(f"foot cannot reach {STEP_REACH:.0f} mm ahead at stance "
                     f"height {STANCE_H:.0f} — no usable step length")
    return fails


def joint_torques() -> dict:
    """Static hold torques per leg at stance, and at worst-case step reach."""
    m = total_mass()
    out = {}
    for tag, fx in (("stand", 0.0), ("reach", STEP_REACH)):
        sol = stance_angles(STANCE_H, fx)
        if sol is None:
            out[tag] = None
            continue
        hip_p, knee_p = sol
        kx, kz = knee_position(hip_p)
        foot_x = kx + LOWER_LEG * math.sin(hip_p + knee_p)
        # Static: the standing robot is carried by all four feet.
        f = m * 9.81 / 4.0
        out[tag] = {
            "hip": abs(f * (foot_x - 0.0)) / 1000.0,      # N.m
            "knee": abs(f * (foot_x - kx)) / 1000.0,
        }
    return out


def check_torque() -> list[str]:
    fails = []
    t = joint_torques()
    for tag, vals in t.items():
        if vals is None:
            continue
        for joint, motor in (("hip", HIP_MOTOR), ("knee", KNEE_MOTOR)):
            avail = MOTORS[motor]["torque"] * (GEAR if GEAR > 1.5 else 1.0)
            if vals[joint] > avail:
                fails.append(f"{joint} at {tag}: {vals[joint]:.2f} N.m needed vs "
                             f"{avail:.2f} from {motor}")
    return fails


def support_polygon_margin() -> float:
    """
    Smallest distance from the body centre to an edge of the foot polygon.

    A rover cannot fall between its wheels; a quadruped can. This criterion has
    no analogue in the rover harness.
    """
    hx, hy = BODY_L * HIP_X_FRAC, BODY_W / 2.0 + ABDUCT_OFF
    return min(hx, hy)


def check_stability() -> list[str]:
    fails = []
    margin = support_polygon_margin()
    if margin < SUPPORT_MARGIN_MIN:
        fails.append(f"support polygon margin {margin:.0f} mm < "
                     f"{SUPPORT_MARGIN_MIN:.0f} mm minimum")
    belly = STANCE_H - BODY_H / 2.0
    if belly < BELLY_CLEARANCE_MIN:
        fails.append(f"belly clearance {belly:.0f} mm < "
                     f"{BELLY_CLEARANCE_MIN:.0f} mm minimum")
    return fails


def check_mount_fit() -> list[str]:
    """Same rule that caught the NEMA23 overhang on the rover."""
    fails = []
    for label, motor, face in (("abduction", ABDUCT_MOTOR, BODY_H),
                               ("hip", HIP_MOTOR, LEG_W),
                               ("knee", KNEE_MOTOR, LEG_W)):
        m = MOTORS[motor]
        need = max(m["bolt_pitch"] / 2.0 + m["hole_d"] / 2.0, m["pilot_d"] / 2.0)
        if need > face / 2.0:
            fails.append(f"{label} {motor} needs {need:.1f} mm of face on a "
                         f"{face / 2.0:.1f} mm surface "
                         f"(overhang {need - face / 2.0:.1f} mm)")
    return fails


# =============================================================================
# HARNESS REGISTRATION
# =============================================================================

def metric_knee_torque():
    t = joint_torques().get("stand")
    avail = MOTORS[KNEE_MOTOR]["torque"] * (GEAR if GEAR > 1.5 else 1.0)
    return (t["knee"] if t else 99.0), avail


def metric_hip_torque():
    t = joint_torques().get("reach")
    avail = MOTORS[HIP_MOTOR]["torque"] * (GEAR if GEAR > 1.5 else 1.0)
    return (t["hip"] if t else 99.0), avail


def metric_mount():
    m = MOTORS[HIP_MOTOR]
    need = max(m["bolt_pitch"] / 2.0 + m["hole_d"] / 2.0, m["pilot_d"] / 2.0)
    return LEG_W / 2.0, need


def metric_mass():
    return total_mass(), 12.0


def metric_support():
    return support_polygon_margin(), SUPPORT_MARGIN_MIN


def check_mass() -> list[str]:
    m = total_mass()
    return [f"{m:.2f} kg over the 12.0 kg budget"] if m > 12.0 else []


def check_knee() -> list[str]:
    v, a = metric_knee_torque()
    return [f"knee {v:.2f} N.m vs {a:.2f} from {KNEE_MOTOR}"] if v > a else []


def check_hip() -> list[str]:
    v, a = metric_hip_torque()
    return [f"hip at reach {v:.2f} N.m vs {a:.2f} from {HIP_MOTOR}"] if v > a else []


CHECKS = (
    ("reach", check_reach, None),
    ("mount_fits", check_mount_fit, metric_mount),
    ("stability", check_stability, metric_support),
    ("knee_torque", check_knee, metric_knee_torque),
    ("hip_torque", check_hip, metric_hip_torque),
    ("mass_budget", check_mass, metric_mass),
)
