"""Engineering parameters for the stationary dual-arm humanoid torso.

Everything the geometry needs is declared here so the model is fully
parametric: actuator envelopes, bearing seats, link lengths, joint limits,
wall thicknesses, clearances, and the electronics payload that has to fit
inside the torso.

Units: millimetres, grams, newton-metres, degrees.
Coordinate convention for the whole robot:
    +X  robot right (the +X arm is the right arm)
    +Y  robot forward
    +Z  up, origin at the underside of the floor plate

Run this file directly to print the actuator sizing budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

G = 9.80665  # m/s^2

# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------

DENSITY = {
    # g/mm^3
    "steel": 7.85e-3,
    "aluminium": 2.70e-3,  # 6061-T6 structural links and housings
    "abs": 1.04e-3,  # printed covers, palm, fingers
    "pcb": 1.90e-3,
    "liion": 2.10e-3,  # packaged 18650 pack, gross density
}


# --------------------------------------------------------------------------
# Rotary actuator catalogue
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Actuator:
    """A frameless-motor + strain-wave-gear joint module.

    Envelope is a cylinder of ``housing_od`` x ``length``. The output flange
    turns about the module axis and carries ``bolt_circle`` x ``n_bolts``
    tapped holes. ``rated_torque`` is the continuous output rating; the joint
    sizing check below requires rated >= static gravity load x SAFETY_FACTOR.
    """

    name: str
    housing_od: float
    length: float
    flange_od: float  # rotating output flange diameter
    flange_thk: float
    bolt_circle: float
    n_bolts: int
    bolt_size: float  # tapped hole nominal, e.g. 4.0 -> M4
    rated_torque: float  # Nm continuous
    peak_torque: float  # Nm
    mass: float  # grams, whole module
    bearing: str  # output bearing designation, keys BEARINGS
    cable_od: float  # power+encoder bundle passing through the hollow shaft
    bore: float  # hollow shaft through-bore diameter


ACTUATORS: dict[str, Actuator] = {
    "A100": Actuator(
        # Shoulder pitch and roll: sized after the first mass pass showed the
        # real arm is ~2x the initial estimate, so 40 Nm no longer covered the
        # static load at the design safety factor.
        name="HDX-100",
        housing_od=110.0,
        length=72.0,
        flange_od=76.0,
        flange_thk=9.0,
        bolt_circle=94.0,
        n_bolts=8,
        bolt_size=5.0,
        rated_torque=75.0,
        peak_torque=150.0,
        mass=1900.0,
        bearing="6812",
        cable_od=9.0,
        bore=25.0,
    ),
    "A80": Actuator(
        name="HDX-80",
        housing_od=90.0,
        length=62.0,
        flange_od=62.0,
        flange_thk=8.0,
        bolt_circle=76.0,
        n_bolts=8,
        bolt_size=4.0,
        rated_torque=40.0,
        peak_torque=82.0,
        mass=1050.0,
        bearing="6810",
        cable_od=8.0,
        bore=20.0,
    ),
    "A60": Actuator(
        name="HDX-60",
        housing_od=70.0,
        length=52.0,
        flange_od=48.0,
        flange_thk=6.0,
        bolt_circle=58.0,
        n_bolts=6,
        bolt_size=3.0,
        rated_torque=18.0,
        peak_torque=38.0,
        mass=620.0,
        bearing="6808",
        cable_od=6.5,
        bore=16.0,
    ),
    "A40": Actuator(
        name="HDX-40",
        housing_od=50.0,
        length=42.0,
        flange_od=32.0,
        flange_thk=5.0,
        bolt_circle=40.0,
        n_bolts=6,
        bolt_size=3.0,
        rated_torque=6.0,
        peak_torque=13.0,
        mass=310.0,
        bearing="6806",
        cable_od=5.0,
        bore=12.0,
    ),
}

# Small geared servo used for the finger drives.
FINGER_SERVO = {
    # Micro geared unit driving one tendon. Sized down from a standard 40 mm
    # servo: four of those did not fit either the palm or the forearm cavity.
    "name": "DSM-30",
    "body": (30.0, 15.0, 32.0),  # W x D x H as installed in the forearm
    "horn_od": 16.0,
    "rated_torque": 1.2,  # Nm at the spool
    "mass": 58.0,
    "bolt_size": 2.5,
    "bolt_span": 24.0,
    "tendon_pitch_r": 6.0,  # spool radius -> 200 N tendon tension
}


# --------------------------------------------------------------------------
# Bearings (metric deep-groove, ISO 15)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bearing:
    designation: str
    bore: float
    od: float
    width: float
    shoulder: float  # recommended housing shoulder height (per ISO mounting)


BEARINGS: dict[str, Bearing] = {
    "6812": Bearing("6812", 60.0, 78.0, 10.0, 3.5),
    "6810": Bearing("6810", 50.0, 65.0, 7.0, 3.0),
    "6808": Bearing("6808", 40.0, 52.0, 7.0, 2.5),
    "6806": Bearing("6806", 30.0, 42.0, 7.0, 2.0),
    "6802": Bearing("6802", 15.0, 24.0, 5.0, 1.5),
    "623": Bearing("623", 3.0, 10.0, 4.0, 1.0),
}

BEARING_PRESS_FIT = 0.012  # H7/k6 nominal interference on the OD seat
BEARING_SEAT_DEPTH_EXTRA = 0.3  # seat cut slightly deeper than race width


# --------------------------------------------------------------------------
# Global build tolerances
# --------------------------------------------------------------------------

WALL = 4.0  # structural aluminium wall
COVER_WALL = 2.5  # non-structural printed cover
JOINT_GAP = 1.5  # running clearance between rotating and fixed shells
FASTENER_CLEAR = {2.5: 2.8, 3.0: 3.4, 4.0: 4.5, 5.0: 5.5, 6.0: 6.6, 12.0: 13.5}
FILLET = 2.0
SAFETY_FACTOR = 2.0  # required actuator rating vs. static gravity torque
PAYLOAD_PER_HAND = 1000.0  # grams, rated grasp load at the palm centre


# --------------------------------------------------------------------------
# Fixed base and pedestal
# --------------------------------------------------------------------------

BASE_PLATE = {
    "size": 420.0,  # square footprint
    "thk": 16.0,  # steel
    "corner_r": 30.0,
    "anchor_bolt": 12.0,  # M12 wedge anchors into concrete
    "anchor_inset": 42.0,  # from plate edge to bolt centre
    "anchor_washer_od": 30.0,
}

COLUMN = {
    "width": 160.0,  # square section
    "height": 700.0,
    "wall": 6.0,
    "corner_r": 12.0,
    "access_w": 90.0,  # rear cable access hatch
    "access_h": 220.0,
    "access_z": 120.0,  # hatch bottom above plate top
    "flange_thk": 12.0,  # top and bottom flanges
    "flange_size": 230.0,
    "flange_bolt": 8.0,
    "flange_bolt_inset": 22.0,
}


# --------------------------------------------------------------------------
# Torso enclosure and its electronics payload
# --------------------------------------------------------------------------

TORSO = {
    "width": 330.0,  # X, shoulder to shoulder direction
    "depth": 210.0,  # Y
    "height": 390.0,  # Z
    "wall": WALL,
    "corner_r": 14.0,
    "shoulder_z": 300.0,  # shoulder axis height above torso floor
    "vent_slot_w": 6.0,
    "vent_slot_h": 60.0,
    "vent_count": 7,
    "fan_size": 80.0,
    "standoff_od": 7.0,
    "standoff_h": 8.0,
    "rail_thk": 3.0,
}


@dataclass(frozen=True)
class Component:
    """An electronics/power item that must physically fit in the torso.

    ``pos`` is the component centre in torso-local coordinates (origin at the
    torso footprint centre, z=0 at the inner floor). ``mount`` lists local
    (x, y) standoff positions relative to the component centre.
    """

    name: str
    size: tuple[float, float, float]
    pos: tuple[float, float, float]
    mass: float  # grams
    material: str
    mount: tuple[tuple[float, float], ...] = ()
    clearance: float = 8.0  # keep-out added around the envelope


def _pcb_mounts(l: float, w: float, inset: float = 4.0) -> tuple[tuple[float, float], ...]:
    x = l / 2 - inset
    y = w / 2 - inset
    return ((-x, -y), (x, -y), (-x, y), (x, y))


# Stacked bottom-up: battery lowest (keeps the centre of mass down and lets
# the pack drop out through the base), then the power stage, then compute,
# then the two motor drivers nearest the shoulder looms. Vertical gaps are set
# by each item's own clearance, which the fit check enforces.
PAYLOAD: tuple[Component, ...] = (
    Component(
        "battery_pack",  # 12S4P 18650, 44.4 V nominal
        (250.0, 90.0, 72.0),
        (0.0, 0.0, 45.0),
        4800.0,
        "liion",
        (),
        clearance=10.0,
    ),
    Component(
        "power_distribution",  # fused PDB + precharge + e-stop relay
        (140.0, 80.0, 26.0),
        (-70.0, 0.0, 112.0),
        350.0,
        "pcb",
        _pcb_mounts(140.0, 80.0),
    ),
    Component(
        "dc_dc_converter",  # 48 V -> 24/12/5 V rail bank
        (120.0, 70.0, 34.0),
        (78.0, 0.0, 116.0),
        300.0,
        "pcb",
        _pcb_mounts(120.0, 70.0),
    ),
    Component(
        "compute_module",  # Jetson-class carrier + SoM
        (110.0, 110.0, 32.0),
        (-75.0, 0.0, 180.0),
        620.0,
        "pcb",
        _pcb_mounts(110.0, 110.0, 5.0),
        clearance=14.0,  # heatsink airflow
    ),
    Component(
        "motor_driver_left",  # 8-channel FOC driver, left arm
        (170.0, 100.0, 22.0),
        (0.0, 0.0, 245.0),
        410.0,
        "pcb",
        _pcb_mounts(170.0, 100.0),
    ),
    Component(
        "motor_driver_right",
        (170.0, 100.0, 22.0),
        (0.0, 0.0, 295.0),
        410.0,
        "pcb",
        _pcb_mounts(170.0, 100.0),
    ),
)

CABLE_CHANNEL = {
    "width": 34.0,
    "depth": 26.0,
    "wall": 2.5,
}


# --------------------------------------------------------------------------
# Arm kinematics
#
# 7-DOF per arm: shoulder pitch / roll / yaw, elbow pitch, wrist yaw / pitch /
# roll. Lengths are the distances between consecutive joint axes.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Joint:
    name: str
    actuator: str  # key into ACTUATORS
    axis: str  # 'x', 'y' or 'z' in the joint's parent frame
    limits: tuple[float, float]  # degrees, for the RIGHT arm
    offset: tuple[float, float, float]  # from parent joint origin, mm


ARM = {
    "shoulder_offset_x": 172.0,  # torso centreline to shoulder pitch axis
    "shoulder_offset_y": 0.0,
    "clavicle_len": 84.0,  # torso wall to shoulder-pitch OUTPUT face
    "shoulder_roll_dx": 92.0,  # pitch output face -> roll axis, outboard
    "shoulder_yaw_drop": 84.0,  # roll output face -> yaw output face
    "upper_arm_len": 280.0,  # shoulder roll axis -> elbow axis
    "forearm_len": 250.0,  # elbow axis -> wrist pitch axis
    "wrist_to_palm": 62.0,  # wrist roll axis -> palm centre
    "link_od": 76.0,  # structural tube outer diameter, upper arm
    "link_od_fore": 76.0,  # forearm tube
    "link_wall": 3.5,
    "yoke_wall": 6.0,
}

# Axis letters are in the RIGHT arm's parent frame at the zero pose (arm
# hanging straight down, palm facing the body):
#   x = lateral  -> pitch (flexion/extension)
#   y = fore-aft -> roll  (abduction/adduction, wrist deviation)
#   z = vertical -> yaw   (long-axis rotation of the limb)
# The left arm mirrors about the YZ plane, which flips the sign of the roll
# and yaw limits; MIRRORED_AXES records which ones.
MIRRORED_AXES = ("y", "z")

WRIST_YAW_DROP = 90.0  # elbow axis -> forearm-roll axis
WRIST_PITCH_DROP = 116.0  # forearm-roll axis -> wrist pitch axis
WRIST_ROLL_DROP = 44.0  # wrist pitch axis -> wrist roll axis

JOINTS: tuple[Joint, ...] = (
    Joint("shoulder_pitch", "A100", "x", (-60.0, 180.0), (0.0, 0.0, 0.0)),
    Joint("shoulder_roll", "A100", "y", (-150.0, 20.0), (ARM["shoulder_roll_dx"], 0.0, 0.0)),
    Joint("shoulder_yaw", "A60", "z", (-90.0, 90.0), (0.0, 0.0, -ARM["shoulder_yaw_drop"])),
    # 145 deg was an anatomy figure. Sweeping the real geometry, the upper arm
    # first touches the forearm tube between 135 and 140 deg, so the limit is
    # set to the last verified-clear angle less a 5 deg margin.
    Joint("elbow_pitch", "A80", "x", (0.0, 130.0), (0.0, 0.0, -ARM["upper_arm_len"])),
    Joint("wrist_yaw", "A40", "z", (-90.0, 90.0), (0.0, 0.0, -WRIST_YAW_DROP)),
    Joint("wrist_pitch", "A40", "x", (-70.0, 70.0), (0.0, 0.0, -WRIST_PITCH_DROP)),
    Joint("wrist_roll", "A40", "y", (-30.0, 30.0), (0.0, 0.0, -WRIST_ROLL_DROP)),
)

assert (
    WRIST_YAW_DROP + WRIST_PITCH_DROP + WRIST_ROLL_DROP == ARM["forearm_len"]
), "wrist joint drops must sum to the forearm length"


HAND = {
    "palm": (96.0, 30.0, 96.0),  # X width, Y thickness, Z length
    "palm_wall": 3.0,
    "n_fingers": 3,
    "finger_pitch": 30.0,  # spacing between finger axes (fork width + clearance)
    "proximal_len": 46.0,
    "distal_len": 34.0,
    "finger_w": 18.0,
    "finger_t": 16.0,
    "knuckle_od": 17.0,
    "proximal_limits": (0.0, 90.0),
    "distal_limits": (0.0, 95.0),
    "thumb_base_limits": (0.0, 95.0),  # opposition rotation
    "thumb_proximal_len": 42.0,
    "thumb_distal_len": 32.0,
    "pad_thk": 2.5,  # elastomer grip pad
    "thumb_pad_inset": 6.0,  # from the palm +X edge to the pad centre
    "thumb_pad_proud": 14.0,  # pad stands proud of the PALMAR face, so the
    #                          thumb sweeps in front of the palm, not through it
    "thumb_pad_top": 6.0,  # pad top below the palm top face
    "thumb_pad_len": 40.0,  # pad length; the thumb knuckle is at its end
    "thumb_cant": 90.0,  # pad rotation about Z: the thumb then flexes in
    #                      the XZ plane, i.e. straight toward the fingers
}


# --------------------------------------------------------------------------
# Derived values
# --------------------------------------------------------------------------

TOTAL_HEIGHT = BASE_PLATE["thk"] + COLUMN["height"] + TORSO["height"]
SHOULDER_HEIGHT = BASE_PLATE["thk"] + COLUMN["height"] + TORSO["shoulder_z"]
MAX_REACH = (
    ARM["clavicle_len"]
    + ARM["upper_arm_len"]
    + ARM["forearm_len"]
    + ARM["wrist_to_palm"]
    + HAND["palm"][2]
)


def clearance_hole(nominal: float) -> float:
    """Normal-fit clearance hole diameter for a metric nominal size."""
    if nominal in FASTENER_CLEAR:
        return FASTENER_CLEAR[nominal]
    return nominal * 1.12


def tap_drill(nominal: float) -> float:
    """Coarse-pitch tapping drill diameter."""
    pitch = {2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0, 12.0: 1.75}
    return nominal - pitch.get(nominal, nominal * 0.16)


# --------------------------------------------------------------------------
# Static torque budget
#
# Link masses here are the a-priori estimates used to pick actuators. The
# assembly script recomputes them from the real solid volumes and re-runs this
# same check, so the estimate below is only the starting point.
# --------------------------------------------------------------------------

LINK_MASS_ESTIMATE = {
    "shoulder_block": 1900.0,
    "upper_arm": 1250.0,
    "forearm": 950.0,
    "wrist": 480.0,
    "hand": 520.0,
}


@dataclass
class Segment:
    """A rigid mass at a distance along the outstretched arm."""

    name: str
    mass: float  # grams
    reach: float  # mm from the shoulder roll axis to this segment's CoM


def outstretched_segments(link_mass: dict[str, float] | None = None) -> list[Segment]:
    """Mass layout with the arm held straight out horizontally.

    This is the worst case for shoulder pitch, elbow pitch and wrist pitch.
    """
    m = dict(LINK_MASS_ESTIMATE)
    if link_mass:
        m.update(link_mass)

    ua = ARM["upper_arm_len"]
    fa = ARM["forearm_len"]
    hand_reach = ua + fa + ARM["wrist_to_palm"] + HAND["palm"][2] / 2

    return [
        Segment("upper_arm", m["upper_arm"], ua / 2),
        Segment("forearm", m["forearm"], ua + fa / 2),
        Segment("wrist", m["wrist"], ua + fa + ARM["wrist_to_palm"] / 2),
        Segment("hand", m["hand"], hand_reach),
        Segment("payload", PAYLOAD_PER_HAND, hand_reach),
    ]


# Distance from the shoulder roll axis to each pitch joint, for the budget.
PITCH_JOINT_REACH = {
    "shoulder_pitch": 0.0,
    "elbow_pitch": ARM["upper_arm_len"],
    "wrist_pitch": ARM["upper_arm_len"] + ARM["forearm_len"],
}


def torque_budget(link_mass: dict[str, float] | None = None) -> list[dict]:
    """Static gravity torque at each pitch joint, arm outstretched.

    Returns one row per joint with the required and available torque.
    """
    segments = outstretched_segments(link_mass)
    joints = {j.name: j for j in JOINTS}
    rows = []

    for joint_name, joint_reach in PITCH_JOINT_REACH.items():
        joint = joints[joint_name]
        act = ACTUATORS[joint.actuator]
        # Only mass outboard of this joint loads it.
        torque = sum(
            (seg.mass / 1000.0) * G * ((seg.reach - joint_reach) / 1000.0)
            for seg in segments
            if seg.reach > joint_reach
        )
        required = torque * SAFETY_FACTOR
        rows.append(
            {
                "joint": joint_name,
                "actuator": act.name,
                "static_Nm": torque,
                "required_Nm": required,
                "rated_Nm": act.rated_torque,
                "margin": act.rated_torque / required if required else float("inf"),
                "ok": act.rated_torque >= required,
            }
        )
    return rows


def base_overturning(link_mass: dict[str, float] | None = None) -> dict:
    """Overturning moment at the floor plate with both arms outstretched.

    Checks the M12 anchors in tension. Anchors are on a square pattern, so the
    worst axis is a plate edge; two anchors resist, two are unloaded.
    """
    segments = outstretched_segments(link_mass)
    arm_mass = sum(s.mass for s in segments)  # includes payload
    # Both arms reach forward (+Y): moment arm is the CoM reach from centreline.
    arm_cog = sum(s.mass * s.reach for s in segments) / arm_mass
    moment = 2 * (arm_mass / 1000.0) * G * (arm_cog / 1000.0)  # Nm, both arms

    lever = BASE_PLATE["size"] - 2 * BASE_PLATE["anchor_inset"]  # bolt-to-bolt span
    tension = moment / (lever / 1000.0) / 2.0  # N, shared by two anchors
    return {
        "arm_mass_g": arm_mass,
        "arm_cog_mm": arm_cog,
        "moment_Nm": moment,
        "bolt_span_mm": lever,
        "anchor_tension_N": tension,
    }


def _report() -> None:
    print(f"Total height      {TOTAL_HEIGHT:8.1f} mm")
    print(f"Shoulder height   {SHOULDER_HEIGHT:8.1f} mm")
    print(f"Max reach/arm     {MAX_REACH:8.1f} mm")
    print()
    print(f"{'joint':<16}{'actuator':<10}{'static':>9}{'req(x2)':>9}{'rated':>8}{'margin':>8}  ok")
    for row in torque_budget():
        print(
            f"{row['joint']:<16}{row['actuator']:<10}"
            f"{row['static_Nm']:>8.2f}N{row['required_Nm']:>8.2f}N"
            f"{row['rated_Nm']:>7.1f}N{row['margin']:>8.2f}  {'PASS' if row['ok'] else 'FAIL'}"
        )
    print()
    ot = base_overturning()
    print(f"Arm+payload mass  {ot['arm_mass_g']:8.0f} g   CoG {ot['arm_cog_mm']:.0f} mm from shoulder")
    print(f"Overturning       {ot['moment_Nm']:8.1f} Nm  (both arms forward)")
    print(f"M12 anchor pull   {ot['anchor_tension_N']:8.0f} N   per loaded anchor")


if __name__ == "__main__":
    _report()
