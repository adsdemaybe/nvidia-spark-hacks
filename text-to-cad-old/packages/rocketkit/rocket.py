"""
Parametric 2.5 ft rocket airframe with RCS thruster ports — third topology test.

762 mm tall (2 ft 6 in). Main thruster hole at the aft end, side thruster ports
sized and placed so the vehicle actually has authority about all three angular
degrees of freedom.

PROVENANCE WARNING
------------------
The NEMA/servo/bearing data used by roverkit and quadkit was verified against
manufacturer drawings by a research pass. The rocket motor figures below were
NOT. They are standard hobby-rocketry nominal values, marked INFERRED, and must
be checked against a real motor datasheet before anything is built or flown.
Tube diameters (24/29/38 mm) are genuine industry standards and are the only
figures here I would call CONFIRMED.

WHAT TRANSFERS from the rover/quadruped harness:
    the "criteria decide, the agent proposes" contract
    the mount-fit rule (a feature must land on material)
    the critic's coverage test
WHAT DOES NOT:
    every physics criterion. A rocket has no joints, no gait, no tip-over. It
    has control authority, thrust-to-weight, and a mass budget instead.

NOT MODELLED — do not read a passing report as flightworthy:
    aerodynamics, centre of pressure, static margin, drag
    thrust curve shape, burn time, or transient response
    propellant feed, plumbing, valve response time
    structural loads, buckling, thermal
    any actual flight dynamics
This checks GEOMETRY and STATIC CONTROL AUTHORITY. Nothing else.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "roverkit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from build123d import (
    Align, Axis, BuildPart, BuildSketch, Circle, Cone, Cylinder, GeomType,
    Location, Locations, Mode, Part, Plane, Polygon, Rot, chamfer, extrude,
    fillet,
)

# =============================================================================
# PARAMETRIC BLOCK
# =============================================================================

TOTAL_H = 762.0                # 2.5 ft, the stated requirement
BODY_D = 100.0
WALL = 3.0
NOSE_L = 170.0
# ---- Fins: aerodynamic stability, and the thing that makes it a rocket SHIP --
# A finless body is statically unstable: its centre of pressure sits ahead of
# the centre of gravity, so any disturbance grows. RCS can fight that but only
# within its authority and only while it has gas. Fins fix it passively.
FIN_COUNT = 4
FIN_ROOT = 190.0               # root chord, mm
FIN_TIP = 80.0                 # tip chord, mm
FIN_SPAN = 95.0                # semi-span out from the body, mm
FIN_SWEEP = 105.0              # leading-edge sweep, mm
FIN_THICK = 5.0
FIN_Z = 8.0                    # root leading edge above the base
MIN_STATIC_MARGIN = 1.0        # calibers; 1-2 is the accepted band

RHO_PLA = 1.24e-6              # kg/mm^3
NOZZLE_MIN_WALL = 4.0          # material around a port before it blows out

# ---- Motor tubes: CONFIRMED industry standards -------------------------------
MOTOR_TUBES = {"24mm": 24.0, "29mm": 29.0, "38mm": 38.0}
MAIN_TUBE = "29mm"
TUBE_CLEARANCE = 0.5           # slip fit for the motor casing

# ---- Motors: INFERRED nominal values, NOT datasheet-verified -----------------
MAIN_MOTORS = {
    "E12":  {"tube": "24mm", "avg_thrust": 11.0, "mass": 0.036, "impulse": 28.0},
    "F32":  {"tube": "29mm", "avg_thrust": 32.0, "mass": 0.081, "impulse": 60.0},
    "G80":  {"tube": "29mm", "avg_thrust": 80.0, "mass": 0.123, "impulse": 120.0},
    "H128": {"tube": "38mm", "avg_thrust": 128.0, "mass": 0.230, "impulse": 240.0},
}
MAIN_MOTOR = "G80"

# ---- RCS: cold gas. INFERRED. -----------------------------------------------
RCS_PORT_D = 8.0               # nozzle throat/port bore
RCS_THRUST = 3.0               # N per port, cold gas at ~6 bar
RCS_COUNT = 4                  # ports in the ring
RCS_STATION = 0.72             # port height as a fraction of TOTAL_H
RCS_CANT = 0.0                 # degrees tangential. 0 = purely radial = NO ROLL

# ---- Curved profile ---------------------------------------------------------
# Nose is an LD-Haack (von Karman) ogive: the minimum-DRAG body of revolution
# for a given length and diameter. Note that the derivation minimises WAVE drag,
# which is a supersonic term; at the Mach 0.3-0.6 this vehicle actually flies it
# is essentially zero. The von Karman nose is still marginally the best subsonic
# nose (lowest pressure drag, no separation), but the reason usually given for
# it does not apply here.
HAACK_C = 0.0                  # 0 = LD-Haack (von Karman), 1/3 = LV-Haack
BOATTAIL_LEN = 0.0             # mm of aft taper. 0 = blunt base = worst case
BOATTAIL_RATIO = 1.0           # base dia / body dia at the aft end
PROFILE_PTS = 44               # spline resolution

# ---- Atmosphere / drag reference (INFERRED, textbook values) -----------------
AIR_RHO = 1.225                # kg/m^3 sea level
AIR_MU = 1.81e-5               # Pa.s
V_REF = 100.0                  # m/s, representative of coast phase
# Budget the drag FORCE, not the coefficient.
#
# Cd is normalised by frontal area, so "minimise Cd" rewards a BIGGER rocket:
# the optimiser inflated BODY_D from 100 to 145 mm, drove Cd from 0.197 to
# 0.113 — the best coefficient of any design tried — and raised actual drag
# from 9.5 N to 11.4 N. A coefficient is not a force. Budget the force.
MAX_DRAG_N = 11.0              # relaxed: a finned rocket ship is not a minimum-drag dart
MAX_CD = 999.0                 # retained only so old references do not break

MIN_AXIS_TORQUE = 60.0         # N.mm required about every angular axis
MIN_TWR = 1.4                  # thrust-to-weight at liftoff

DESIGN_VARS = {
    "BODY_D": (70.0, 160.0),
    "WALL": (2.0, 6.0),
    "NOSE_L": (90.0, 260.0),
    "RCS_PORT_D": (4.0, 16.0),
    "RCS_STATION": (0.52, 0.92),
    "RCS_CANT": (0.0, 75.0),
    "RCS_COUNT": (4.0, 8.0),
    "BOATTAIL_LEN": (0.0, 220.0),
    "BOATTAIL_RATIO": (0.45, 1.0),
    "FIN_ROOT": (90.0, 260.0),
    "FIN_TIP": (30.0, 140.0),
    "FIN_SPAN": (45.0, 150.0),
    "FIN_SWEEP": (0.0, 170.0),
}

DISCRETE_VARS = {
    "MAIN_MOTOR": tuple(MAIN_MOTORS),
}


def current_design() -> dict:
    g = globals()
    d = {k: g[k] for k in DESIGN_VARS}
    d.update({k: g[k] for k in DISCRETE_VARS})
    return d


def reconfigure(**overrides) -> None:
    g = globals()
    for k, v in overrides.items():
        if k in DISCRETE_VARS:
            if v not in MAIN_MOTORS:
                raise KeyError(f"{v!r} is not a catalogued motor")
            g[k] = v
            g["MAIN_TUBE"] = MAIN_MOTORS[v]["tube"]
        elif k in DESIGN_VARS:
            g[k] = float(v)
        else:
            raise KeyError(f"{k} is not a design variable")
    g["MAIN_TUBE"] = MAIN_MOTORS[MAIN_MOTOR]["tube"]


# =============================================================================
# CONTROL AUTHORITY — the criterion this vehicle exists to satisfy
# =============================================================================

def rcs_geometry():
    """Position and thrust direction of every RCS port, in body coordinates."""
    n = int(round(RCS_COUNT))
    r = BODY_D / 2.0
    z = TOTAL_H * RCS_STATION
    cant = math.radians(RCS_CANT)
    out = []
    for k in range(n):
        a = 2 * math.pi * k / n
        pos = np.array([r * math.cos(a), r * math.sin(a), z])
        radial = np.array([-math.cos(a), -math.sin(a), 0.0])
        tangential = np.array([-math.sin(a), math.cos(a), 0.0])
        # Canting the nozzle trades radial thrust for a tangential component,
        # which is the ONLY way a ring of side thrusters gets roll authority.
        direction = radial * math.cos(cant) + tangential * math.sin(cant)
        out.append((pos, direction / np.linalg.norm(direction), a))
    return out


def torque_matrix(cg_z: float | None = None) -> np.ndarray:
    """3 x N matrix of torque per unit thrust, about the centre of mass."""
    z_cg = cg_z if cg_z is not None else centre_of_mass()
    cols = []
    for pos, direction, _ in rcs_geometry():
        r = pos - np.array([0.0, 0.0, z_cg])
        cols.append(np.cross(r, direction) * RCS_THRUST)
    return np.array(cols).T


def control_authority() -> dict:
    """Per-axis torque and the rank of the control matrix."""
    M = torque_matrix()
    rank = int(np.linalg.matrix_rank(M, tol=1e-6))
    # Best achievable torque about each axis using all ports together.
    axes = {}
    for i, name in enumerate(("pitch", "yaw", "roll")):
        axes[name] = float(np.abs(M[i]).sum())
    return {"rank": rank, "axes": axes, "matrix": M}


def check_control() -> list[str]:
    """
    The vehicle must have independent authority about all three angular axes.

    Failure mode this exists to catch: a ring of purely radial thrusters. It
    looks obviously correct — four ports, evenly spaced, clearly "controls
    attitude" — and it has a torque matrix of rank 2 with identically zero roll
    authority, because a radial thrust vector has no tangential moment arm. The
    vehicle can pitch and yaw and will spin uncontrollably about its long axis.
    """
    fails = []
    ca = control_authority()
    if ca["rank"] < 3:
        fails.append(
            f"control matrix rank {ca['rank']} of 3 — the RCS cannot command "
            f"all three angular axes; "
            f"{', '.join(k for k, v in ca['axes'].items() if v < 1e-6)} "
            f"has no authority at all")
    for name, tq in ca["axes"].items():
        if tq < MIN_AXIS_TORQUE:
            fails.append(f"{name} authority {tq:.0f} N.mm below the "
                         f"{MIN_AXIS_TORQUE:.0f} N.mm minimum")
    return fails


def metric_control():
    ca = control_authority()
    return min(ca["axes"].values()), MIN_AXIS_TORQUE


# =============================================================================
# MASS AND PERFORMANCE
# =============================================================================

def airframe_mass() -> float:
    return build_airframe().volume * RHO_PLA


def total_mass() -> float:
    return airframe_mass() + MAIN_MOTORS[MAIN_MOTOR]["mass"] + 0.35   # avionics


def centre_of_mass() -> float:
    """Height of the CG above the base, from the real solid plus the motor."""
    body = build_airframe()
    m_body = body.volume * RHO_PLA
    z_body = body.center().Z
    m_motor = MAIN_MOTORS[MAIN_MOTOR]["mass"]
    z_motor = 60.0
    m_av, z_av = 0.35, TOTAL_H * 0.45
    tot = m_body + m_motor + m_av
    return (m_body * z_body + m_motor * z_motor + m_av * z_av) / tot


def check_twr() -> list[str]:
    t = MAIN_MOTORS[MAIN_MOTOR]["avg_thrust"]
    w = total_mass() * 9.81
    twr = t / w
    if twr < MIN_TWR:
        return [f"thrust-to-weight {twr:.2f} below the {MIN_TWR:.2f} minimum "
                f"({t:.0f} N vs {w:.1f} N)"]
    return []


def metric_twr():
    return (MAIN_MOTORS[MAIN_MOTOR]["avg_thrust"] / (total_mass() * 9.81),
            MIN_TWR)


def check_geometry() -> list[str]:
    """Ports must fit the airframe with material left around them."""
    fails = []
    tube = MOTOR_TUBES[MAIN_TUBE] + TUBE_CLEARANCE
    if tube + 2 * WALL > BODY_D:
        fails.append(f"{MAIN_TUBE} motor tube ({tube:.1f} mm) plus walls "
                     f"exceeds the {BODY_D:.0f} mm body diameter")
    n = int(round(RCS_COUNT))
    circ = math.pi * BODY_D / n
    if RCS_PORT_D + 2 * NOZZLE_MIN_WALL > circ:
        fails.append(f"{n} ports of {RCS_PORT_D:.0f} mm leave under "
                     f"{NOZZLE_MIN_WALL:.0f} mm between them "
                     f"({circ:.0f} mm of circumference each)")
    if RCS_PORT_D > BODY_D * 0.3:
        fails.append(f"port {RCS_PORT_D:.0f} mm is over 30% of body diameter")
    body_h = TOTAL_H - NOSE_L
    if TOTAL_H * RCS_STATION > body_h:
        fails.append(f"RCS station {TOTAL_H * RCS_STATION:.0f} mm is inside the "
                     f"nose cone (body ends at {body_h:.0f} mm)")
    return fails


def check_height() -> list[str]:
    got = build_rocket().bounding_box().size.Z
    if abs(got - TOTAL_H) > 1.0:
        return [f"built height {got:.1f} mm != {TOTAL_H:.0f} mm requirement"]
    return []


# =============================================================================
# CURVED PROFILE AND SUBSONIC DRAG
# =============================================================================

def haack_radius(x: float, length: float, radius: float) -> float:
    """LD-Haack / von Karman ogive radius at station x from the tip."""
    t = min(max(x / length, 0.0), 1.0)
    theta = math.acos(1.0 - 2.0 * t)
    val = theta - math.sin(2.0 * theta) / 2.0 + HAACK_C * math.sin(theta) ** 3
    return radius / math.sqrt(math.pi) * math.sqrt(max(val, 0.0))


def profile() -> list[tuple[float, float]]:
    """
    Outer profile as (radius, z), strictly increasing in z from base to tip.

    Three curved segments: boat-tail, cylindrical mid-body carrying the motor
    and RCS, then the von Karman nose. Points are de-duplicated because the
    segments share endpoints and a spline through a repeated point fails.
    """
    R = BODY_D / 2.0
    bt = max(BOATTAIL_LEN, 0.0)
    r_base = R * BOATTAIL_RATIO
    body_top = TOTAL_H - NOSE_L
    pts: list[tuple[float, float]] = []

    if bt > 1e-6:
        # Cosine blend, tangent to the cylinder at the top so the flow does not
        # see a corner and separate.
        n = max(6, PROFILE_PTS // 3)
        for i in range(n + 1):
            f = i / n
            pts.append((r_base + (R - r_base) * (1.0 - math.cos(f * math.pi / 2.0)),
                        f * bt))
    else:
        pts.append((R, 0.0))

    pts.append((R, body_top))

    # Nose, from just above the shoulder to the tip.
    n = PROFILE_PTS
    for i in range(1, n):
        x = NOSE_L * i / n                    # distance measured from the tip
        pts.append((max(haack_radius(x, NOSE_L, R), 0.05), TOTAL_H - x))
    pts.append((0.05, TOTAL_H))

    pts.sort(key=lambda q: q[1])
    out: list[tuple[float, float]] = []
    for r, z in pts:
        if out and z - out[-1][1] < 1e-6:     # drop shared endpoints
            continue
        out.append((r, z))
    return out


def wetted_area_mm2() -> float:
    """Surface of revolution by Pappus: S = 2*pi*integral(r ds)."""
    pts = profile()
    s = 0.0
    for (r0, z0), (r1, z1) in zip(pts, pts[1:]):
        ds = math.hypot(r1 - r0, z1 - z0)
        s += math.pi * (r0 + r1) * ds
    return s


def drag() -> dict:
    """
    Subsonic drag build-up: skin friction plus base drag.

    Wave drag is deliberately omitted — it is zero at this vehicle's Mach
    number, and including a supersonic term would flatter the design for a
    reason that does not apply. INFERRED textbook correlations, not validated
    against wind-tunnel data.
    """
    L = TOTAL_H / 1000.0
    D = BODY_D / 1000.0
    Re = AIR_RHO * V_REF * L / AIR_MU
    cf = 0.455 / (math.log10(Re) ** 2.58)          # turbulent flat plate
    fineness = L / D
    form = 1.0 + 60.0 / fineness ** 3 + 0.0025 * fineness
    s_ref = math.pi * (BODY_D / 2.0) ** 2
    cd_fric = cf * form * wetted_area_mm2() / s_ref
    d_base = BODY_D * BOATTAIL_RATIO
    # Hoerner base drag: a boat-tail cuts it as the cube of the diameter ratio.
    cd_base = 0.029 * (d_base / BODY_D) ** 3 / math.sqrt(max(cd_fric, 1e-6))
    return {"Re": Re, "cf": cf, "cd_friction": cd_fric, "cd_base": cd_base,
            "cd": cd_fric + cd_base, "s_ref_mm2": s_ref,
            "drag_area_mm2": (cd_fric + cd_base) * s_ref,
            "wetted_mm2": wetted_area_mm2(), "mach": V_REF / 343.0}


def drag_force_n() -> float:
    d = drag()
    return 0.5 * AIR_RHO * V_REF ** 2 * d["cd"] * (d["s_ref_mm2"] * 1e-6)


def check_drag() -> list[str]:
    d = drag()
    f = drag_force_n()
    if f > MAX_DRAG_N:
        return [f"drag {f:.2f} N exceeds the {MAX_DRAG_N:.2f} N budget "
                f"(Cd {d['cd']:.3f} on {d['s_ref_mm2']:.0f} mm2 frontal area; "
                f"friction {d['cd_friction']:.3f} + base {d['cd_base']:.3f})"]
    return []


def metric_drag():
    return drag_force_n(), MAX_DRAG_N


def check_boattail() -> list[str]:
    """The boat-tail must not choke the nozzle or exceed the body length."""
    fails = []
    d_base = BODY_D * BOATTAIL_RATIO
    need = MOTOR_TUBES[MAIN_TUBE] + TUBE_CLEARANCE + 2 * WALL
    if d_base < need:
        fails.append(f"boat-tail base {d_base:.0f} mm is smaller than the "
                     f"{need:.0f} mm needed for the {MAIN_TUBE} motor plus walls")
    if BOATTAIL_LEN > (TOTAL_H - NOSE_L) * 0.75:
        fails.append(f"boat-tail {BOATTAIL_LEN:.0f} mm leaves too little "
                     f"cylindrical body")
    if TOTAL_H * RCS_STATION < BOATTAIL_LEN:
        fails.append("RCS ports fall inside the boat-tail taper")
    return fails


def metric_boattail():
    return BODY_D * BOATTAIL_RATIO, MOTOR_TUBES[MAIN_TUBE] + TUBE_CLEARANCE + 2 * WALL


# =============================================================================
# AERODYNAMIC STABILITY (Barrowman)
# =============================================================================

def barrowman_cp() -> dict:
    """
    Centre of pressure by the Barrowman equations, measured from the BASE.

    Standard subsonic method used throughout model rocketry. Body tubes
    contribute no normal force; the nose, any diameter transition, and the fins
    do. INFERRED status: textbook method, not validated here against tunnel data.
    """
    d = BODY_D
    R = d / 2.0
    base_to_nose_tip = TOTAL_H

    # Nose: Cn = 2 always; centroid at 0.466*L for an ogive.
    cn_nose = 2.0
    x_nose = base_to_nose_tip - 0.466 * NOSE_L

    terms = [(cn_nose, x_nose)]

    # Boat-tail: a shrinking diameter gives a NEGATIVE normal force that pulls
    # the CP forward (destabilising) — the cost of the drag win.
    if BOATTAIL_LEN > 1e-6 and BOATTAIL_RATIO < 1.0:
        d_aft = d * BOATTAIL_RATIO
        cn_bt = 2.0 * ((d_aft / d) ** 2 - 1.0)
        x_bt = (BOATTAIL_LEN / 3.0) * (1.0 + (1.0 - d / d_aft) /
                                       (1.0 - (d / d_aft) ** 2))
        terms.append((cn_bt, x_bt))

    # Fins.
    if FIN_COUNT >= 3 and FIN_SPAN > 1e-6:
        n = int(FIN_COUNT)
        s_span = FIN_SPAN
        cr, ct, sweep = FIN_ROOT, FIN_TIP, FIN_SWEEP
        lf = math.hypot(sweep + ct / 2.0 - cr / 2.0, s_span)
        kfb = 1.0 + R / (s_span + R)                  # body interference
        cn_fin = (kfb * (4.0 * n * (s_span / d) ** 2) /
                  (1.0 + math.sqrt(1.0 + (2.0 * lf / (cr + ct)) ** 2)))
        x_le = FIN_Z + cr                             # from base to root LE top
        x_f = (FIN_Z + cr
               - (sweep * (cr + 2 * ct) / (3.0 * (cr + ct))
                  + (cr + ct - cr * ct / (cr + ct)) / 3.0))
        terms.append((cn_fin, x_f))

    cn_total = sum(c for c, _ in terms)
    cp = sum(c * x for c, x in terms) / cn_total if abs(cn_total) > 1e-9 else 0.0
    return {"cp_from_base": cp, "cn_total": cn_total,
            "ill_conditioned": cn_total < 0.5,
            "terms": terms, "cg_from_base": centre_of_mass()}


def static_margin() -> float:
    """
    Static margin in calibers. Positive = stable.

    Both stations are measured FROM THE BASE, so "aft" is the SMALLER number and
    stability requires cp_from_base < cg_from_base. Writing this the other way
    round reported a finless dart as 21 calibers stable and a properly finned
    rocket as unstable — exactly backwards.
    """
    b = barrowman_cp()
    return (b["cg_from_base"] - b["cp_from_base"]) / BODY_D


def check_stability() -> list[str]:
    b = barrowman_cp()
    if b["ill_conditioned"]:
        # A large boat-tail nearly cancels the nose term, so the CP divide blows
        # up and reports positions outside the vehicle. Refuse to certify rather
        # than quote a meaningless number.
        return [f"Barrowman is ill-conditioned here (total Cn {b['cn_total']:.2f}"
                f" < 0.5): the boat-tail nearly cancels the nose, so CP is not"
                f" trustworthy. Add fin area."]
    sm = static_margin()
    if sm < MIN_STATIC_MARGIN:
        b = barrowman_cp()
        return [f"static margin {sm:.2f} calibers below the "
                f"{MIN_STATIC_MARGIN:.1f} minimum "
                f"(CP {b['cp_from_base']:.0f} mm, CG {b['cg_from_base']:.0f} mm "
                f"from base) — the vehicle is aerodynamically unstable"]
    return []


def metric_stability():
    return static_margin(), MIN_STATIC_MARGIN


# =============================================================================
# CAD
# =============================================================================

def profile_segments(inset: float = 0.0):
    """
    Profile split into (boattail_pts, cyl_top_z, nose_pts).

    Kept as separate curves on purpose: one spline through the boat-tail AND the
    cylinder overshoots the body diameter badly — it bulged a 100 mm body out to
    129.5 mm — because a spline interpolating a curve that flattens into a
    straight line rings past it. Segmenting removes the overshoot entirely.
    """
    R = BODY_D / 2.0 - inset
    r_base = BODY_D / 2.0 * BOATTAIL_RATIO - inset
    bt = max(BOATTAIL_LEN, 0.0)
    body_top = TOTAL_H - NOSE_L

    boat = []
    if bt > 1e-6:
        n = max(6, PROFILE_PTS // 3)
        for i in range(n + 1):
            f = i / n
            boat.append((max(r_base + (R - r_base) * (1 - math.cos(f * math.pi / 2)),
                             0.05), f * bt))
    nose = []
    n = PROFILE_PTS
    for i in range(1, n):
        x = NOSE_L * i / n
        nose.append((max(haack_radius(x, NOSE_L, BODY_D / 2.0) - inset, 0.05),
                     TOTAL_H - x))
    nose.sort(key=lambda q: q[1])
    nose = [(r, z) for r, z in nose if z > body_top + 1e-6]
    return boat, body_top, nose, R, r_base, bt


def _revolved(inset: float, dz: float) -> Part:
    """Revolve the segmented profile. `inset` builds the inner shell surface."""
    from build123d import BuildLine, Spline, Line, make_face, revolve
    boat, body_top, nose, R, r_base, bt = profile_segments(inset)
    z0 = dz
    tip_z = TOTAL_H - dz
    with BuildPart() as p:
        with BuildSketch(Plane.XZ) as sk:
            with BuildLine():
                start = (r_base, z0) if bt > 1e-6 else (R, z0)
                if bt > 1e-6:
                    Spline(*[(r, max(z, z0)) for r, z in boat])
                    Line((boat[-1][0], boat[-1][1]), (R, body_top))
                else:
                    Line(start, (R, body_top))
                Spline(*([(R, body_top)] + [(r, min(z, tip_z)) for r, z in nose]
                         + [(0.05, tip_z)]))
                Line((0.05, tip_z), (0.0, tip_z))
                Line((0.0, tip_z), (0.0, z0))
                Line((0.0, z0), start)
            make_face()
        revolve(axis=Axis.Z)
    return p.part


def build_airframe() -> Part:
    """Curved hollow airframe with the main motor hole and the RCS ports."""
    from build123d import revolve
    with BuildPart() as p:
        outer = _revolved(0.0, 0.0)
        from build123d import add
        add(outer)
        inner = _revolved(WALL, WALL)
        add(inner, mode=Mode.SUBTRACT)

        with BuildSketch(Plane.XY) as m:
            Circle((MOTOR_TUBES[MAIN_TUBE] + TUBE_CLEARANCE) / 2.0)
        extrude(to_extrude=m.sketch, amount=(TOTAL_H - NOSE_L) * 0.35,
                mode=Mode.SUBTRACT)

        z = TOTAL_H * RCS_STATION
        for pos, direction, ang in rcs_geometry():
            plane = Plane(origin=(float(pos[0]), float(pos[1]), z),
                          z_dir=(float(-direction[0]), float(-direction[1]), 0.0))
            with BuildSketch(plane) as s:
                Circle(RCS_PORT_D / 2.0)
            extrude(to_extrude=s.sketch, amount=-BODY_D, mode=Mode.SUBTRACT)
    p.part.label = "airframe"
    return p.part


def build_fins() -> Part:
    """N trapezoidal swept fins around the aft end."""
    from build123d import Polygon, add
    if FIN_COUNT < 3 or FIN_SPAN <= 1e-6:
        return None
    R = BODY_D / 2.0 * BOATTAIL_RATIO if BOATTAIL_LEN > FIN_Z else BODY_D / 2.0
    pts = [(R - 2.0, FIN_Z),
           (R - 2.0, FIN_Z + FIN_ROOT),
           (R + FIN_SPAN, FIN_Z + FIN_SWEEP + FIN_TIP),
           (R + FIN_SPAN, FIN_Z + FIN_SWEEP)]
    with BuildPart() as f:
        for k in range(int(FIN_COUNT)):
            ang = 360.0 * k / int(FIN_COUNT)
            with BuildSketch(Plane.XZ.rotated((0, 0, ang))) as sk:
                Polygon(*pts, align=None)
            extrude(to_extrude=sk.sketch, amount=FIN_THICK / 2.0, both=True)
    f.part.label = "fins"
    return f.part


def build_rocket():
    from build123d import Compound
    body = build_airframe()
    fins = build_fins()
    if fins is None:
        return body
    return Compound(label="rocket", children=[body, fins])


# =============================================================================
# HARNESS REGISTRATION
# The generic harness in packages/harness.py drives any model that exposes
# this. Metric functions matter: a purely boolean criterion is invisible to the
# critic's coverage test, which works on magnitude.
# =============================================================================

def metric_height():
    return build_rocket().bounding_box().size.Z, TOTAL_H


def metric_port_spacing():
    n = int(round(RCS_COUNT))
    return math.pi * BODY_D / n - RCS_PORT_D, 2 * NOZZLE_MIN_WALL


def metric_rank():
    return float(control_authority()["rank"]), 3.0


CHECKS = (
    ("height", check_height, metric_height),
    ("static_margin", check_stability, metric_stability),
    ("drag", check_drag, metric_drag),
    ("boattail", check_boattail, metric_boattail),
    ("geometry", check_geometry, metric_port_spacing),
    ("control_rank", check_control, metric_rank),
    ("control_authority", check_control, metric_control),
    ("thrust_to_weight", check_twr, metric_twr),
)
