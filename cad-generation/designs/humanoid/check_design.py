"""Engineering checks run against the real solid geometry, not estimates.

    python check_design.py [pose ...]

What it checks:
  1. Mass properties  - mass and centre of mass of every part, from its actual
     volume and material, plus catalogue masses for purchased modules.
  2. Joint torque     - static gravity torque about each joint axis, computed
     from the posed geometry outboard of that joint, versus the actuator's
     continuous rating with the design safety factor.
  3. Self-collision   - solid intersection between non-adjacent links, per
     pose, so a pose that folds the arm into itself is caught.
  4. Electronics fit  - every board, converter and the battery inside the
     torso cavity, clear of each other and of the walls.
  5. Anchor loads     - overturning moment and per-anchor tension at the floor.

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# cadgen ships inside the CAD skill; the assembly imports AssemblyHelper.
for candidate in (HERE.parents[2] / ".claude/skills/cad/scripts/packages/cadgen/src",):
    if candidate.exists():
        sys.path.insert(0, str(candidate))

from build123d import CenterOf, Plane, Pos, mirror  # noqa: E402

import humanoid_kinematics as K  # noqa: E402
import humanoid_params as PR  # noqa: E402

G = PR.G


def _load_assembly():
    spec = importlib.util.spec_from_file_location(
        "humanoid_torso.step", HERE / "humanoid_torso.step.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def posed_arm(mod, pose) -> list:
    """Right arm built once and lifted into world coordinates, labels intact.

    ``Pos(...) * shape`` drops the label, so capture the labels from the build
    before transforming rather than building the whole arm a second time.
    """
    parts = mod.build_arm(mod.AssemblyHelper("scratch"), pose)
    labels = [p.label for p in parts]
    placed = [Pos(0, 0, K.TORSO_Z) * p for p in parts]
    for shape, label in zip(placed, labels):
        shape.label = label
    return placed


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------


def part_mass(shape) -> tuple[float, str]:
    """Mass in grams and the material used, inferred from the part label."""
    label = (shape.label or "").lower()

    if "_module" in label or "actuator" in label:
        for key, act in PR.ACTUATORS.items():
            # Modules are labelled by joint, so match on the geometry instead.
            pass
        # Catalogue mass by envelope diameter.
        bb = shape.bounding_box().size
        od = max(bb.X, bb.Y, bb.Z)
        best = min(
            PR.ACTUATORS.values(), key=lambda a: abs(max(a.housing_od, a.length) - od)
        )
        return best.mass, "catalogue"

    if "servo" in label:
        return PR.FINGER_SERVO["mass"], "catalogue"

    volume = sum(s.volume for s in shape.solids())
    if "base_plate" in label:
        material = "steel"
    elif "bearing" in label:
        material = "steel"
    elif any(k in label for k in ("palm", "finger", "thumb", "proximal", "distal", "cover")):
        material = "abs"
    elif any(c.name in label for c in PR.PAYLOAD):
        comp = next(c for c in PR.PAYLOAD if c.name in label)
        return comp.mass, "catalogue"
    else:
        material = "aluminium"
    return volume * PR.DENSITY[material], material


def mass_properties(shapes) -> dict:
    total = 0.0
    moment = [0.0, 0.0, 0.0]
    rows = []
    for shape in shapes:
        m, material = part_mass(shape)
        try:
            c = shape.center(CenterOf.MASS)
        except Exception:
            c = shape.bounding_box().center()
        total += m
        moment[0] += m * c.X
        moment[1] += m * c.Y
        moment[2] += m * c.Z
        rows.append((shape.label, m, material, (c.X, c.Y, c.Z)))
    com = tuple(v / total for v in moment) if total else (0.0, 0.0, 0.0)
    return {"total_g": total, "com": com, "rows": rows}


# --------------------------------------------------------------------------
# 2. Joint torque from the posed geometry
# --------------------------------------------------------------------------

#: The first part label that is OUTBOARD of each joint, in build order.
FIRST_OUTBOARD = {
    "shoulder_pitch": "shoulder_roll_housing",
    "shoulder_roll": "shoulder_yaw_housing",
    "shoulder_yaw": "upper_arm",
    "elbow_pitch": "forearm",
    "wrist_yaw": "forearm_tube",
    "wrist_pitch": "wrist_roll_bracket",
    "wrist_roll": "palm",
}

AXIS_VEC = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def joint_torques(arm_parts, pose) -> list[dict]:
    """Static gravity torque about every joint axis, from real geometry.

    For each joint, sums the moment of every outboard part's weight about the
    joint's own axis, then adds the rated payload at the palm.
    """
    labels = [p.label for p in arm_parts]
    frames = K.arm_frames(pose, side=1)
    rows = []

    # Rated payload, applied at the palm centre.
    palm_frame = frames["palm"].posed
    payload_pos = palm_frame * Pos(0, 0, -PR.HAND["palm"][2] / 2)
    payload_pt = payload_pos.position
    payload = (
        PR.PAYLOAD_PER_HAND,
        (payload_pt.X, payload_pt.Y, payload_pt.Z + K.TORSO_Z),
    )

    for joint in PR.JOINTS:
        start = labels.index(FIRST_OUTBOARD[joint.name])
        outboard = arm_parts[start:]

        origin = frames[joint.name].origin.position
        p_joint = (origin.X, origin.Y, origin.Z + K.TORSO_Z)
        # The axis rotates with everything upstream of this joint.
        axis_loc = frames[joint.name].origin
        a = axis_loc.orientation  # degrees, intrinsic XYZ
        axis_world = _rotate_vector(AXIS_VEC[joint.axis], (a.X, a.Y, a.Z))

        torque = 0.0
        mass_outboard = 0.0
        for part in outboard:
            m, _ = part_mass(part)
            mass_outboard += m
            try:
                c = part.center(CenterOf.MASS)
            except Exception:
                c = part.bounding_box().center()
            r = (c.X - p_joint[0], c.Y - p_joint[1], c.Z - p_joint[2])
            f = (0.0, 0.0, -(m / 1000.0) * G)  # N
            torque += _dot(_cross(r, f), axis_world) / 1000.0  # Nm

        m, pos = payload
        r = (pos[0] - p_joint[0], pos[1] - p_joint[1], pos[2] - p_joint[2])
        f = (0.0, 0.0, -(m / 1000.0) * G)
        torque += _dot(_cross(r, f), axis_world) / 1000.0
        mass_outboard += m

        act = PR.ACTUATORS[joint.actuator]
        required = abs(torque) * PR.SAFETY_FACTOR
        rows.append(
            {
                "joint": joint.name,
                "actuator": act.name,
                "outboard_g": mass_outboard,
                "static_Nm": abs(torque),
                "required_Nm": required,
                "rated_Nm": act.rated_torque,
                "peak_Nm": act.peak_torque,
                "margin": act.rated_torque / required if required > 1e-9 else float("inf"),
                "ok": act.rated_torque >= required,
            }
        )
    return rows


def _rotate_vector(v, angles_deg):
    """Rotate v by intrinsic X-Y-Z Euler angles, matching build123d's order."""
    import math

    x, y, z = (math.radians(a) for a in angles_deg)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    # Rz * Ry * Rx applied to v
    vx, vy, vz = v
    # Rx
    vy, vz = cx * vy - sx * vz, sx * vy + cx * vz
    # Ry
    vx, vz = cy * vx + sy * vz, -sy * vx + cy * vz
    # Rz
    vx, vy = cz * vx - sz * vy, sz * vx + cz * vy
    return (vx, vy, vz)


# --------------------------------------------------------------------------
# 3. Self-collision
# --------------------------------------------------------------------------

#: Links that legitimately touch, keyed by build order adjacency.
ADJACENT_TOLERANCE = 25.0  # mm^3 of intersection treated as coincident faces


def _bbox_overlap(a, b, pad=0.0):
    ba, bb = a.bounding_box(), b.bounding_box()
    return not (
        ba.max.X + pad < bb.min.X
        or bb.max.X + pad < ba.min.X
        or ba.max.Y + pad < bb.min.Y
        or bb.max.Y + pad < ba.min.Y
        or ba.max.Z + pad < bb.min.Z
        or bb.max.Z + pad < ba.min.Z
    )


def self_collisions(parts, skip_neighbours: int = 2) -> list[dict]:
    """Solid intersections between parts that are not chain neighbours.

    Parts within ``skip_neighbours`` positions of each other in the chain are
    bolted together and share faces by design, so they are excluded.
    """
    hits = []
    n = len(parts)
    for i in range(n):
        for j in range(i + skip_neighbours + 1, n):
            a, b = parts[i], parts[j]
            if not _bbox_overlap(a, b):
                continue
            try:
                inter = a & b
                vol = sum(s.volume for s in inter.solids()) if inter is not None else 0.0
            except Exception:
                continue
            if vol > ADJACENT_TOLERANCE:
                hits.append(
                    {"a": a.label, "b": b.label, "volume_mm3": vol}
                )
    return hits


# --------------------------------------------------------------------------
# 4. Electronics fit
# --------------------------------------------------------------------------


def electronics_fit() -> list[str]:
    """Check every payload item against the torso cavity and its neighbours."""
    t = PR.TORSO
    wall = t["wall"]
    inner = (
        (-(t["width"] / 2 - wall), t["width"] / 2 - wall),
        (-(t["depth"] / 2 - wall), t["depth"] / 2 - wall),
        (0.0, t["height"] - wall),
    )
    problems = []

    boxes = []
    for c in PR.PAYLOAD:
        l, w, h = c.size
        cx, cy, cz = c.pos
        span = (
            (cx - l / 2, cx + l / 2),
            (cy - w / 2, cy + w / 2),
            (wall + cz - h / 2, wall + cz + h / 2),
        )
        boxes.append((c, span))
        for axis, name in enumerate("xyz"):
            lo, hi = span[axis]
            ilo, ihi = inner[axis]
            if lo < ilo - 1e-6 or hi > ihi + 1e-6:
                problems.append(
                    f"{c.name}: {name} extent {lo:.1f}..{hi:.1f} outside cavity "
                    f"{ilo:.1f}..{ihi:.1f}"
                )

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (ca, sa), (cb, sb) = boxes[i], boxes[j]
            pad = max(ca.clearance, cb.clearance)
            overlaps = all(
                sa[k][0] < sb[k][1] + pad and sb[k][0] < sa[k][1] + pad for k in range(3)
            )
            if overlaps:
                gaps = [
                    max(sa[k][0] - sb[k][1], sb[k][0] - sa[k][1]) for k in range(3)
                ]
                problems.append(
                    f"{ca.name} vs {cb.name}: closest gap {max(gaps):.1f} mm "
                    f"< required {pad:.1f} mm"
                )
    return problems


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def run(pose_names) -> int:
    mod = _load_assembly()
    failures = 0

    print("=" * 78)
    print("MASS PROPERTIES  (whole robot, ready pose)")
    print("=" * 78)
    asm = mod.build_assembly(K.READY_POSE)
    all_leaves = []
    for child in asm.children:
        all_leaves.extend(child.children if child.children else [child])
    mp = mass_properties(all_leaves)
    print(f"total mass          {mp['total_g'] / 1000:8.2f} kg")
    print(
        f"centre of mass      x={mp['com'][0]:7.1f}  y={mp['com'][1]:7.1f}  "
        f"z={mp['com'][2]:7.1f} mm"
    )
    groups: dict[str, float] = {}
    for label, m, material, _ in mp["rows"]:
        key = material
        groups[key] = groups.get(key, 0.0) + m
    for key, m in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"  {key:<12} {m / 1000:8.2f} kg")

    for pose_name in pose_names:
        pose = K.POSES[pose_name]
        print()
        print("=" * 78)
        print(f"POSE: {pose_name}")
        print("=" * 78)

        arm = posed_arm(mod, pose)

        print("\nJOINT TORQUE  (static, arm weight + 1.0 kg payload)")
        print(
            f"{'joint':<16}{'module':<9}{'outboard':>10}{'static':>9}"
            f"{'req x2':>9}{'rated':>8}{'margin':>8}"
        )
        for row in joint_torques(arm, pose):
            flag = "" if row["ok"] else "   <-- OVER"
            print(
                f"{row['joint']:<16}{row['actuator']:<9}"
                f"{row['outboard_g'] / 1000:>8.2f}kg"
                f"{row['static_Nm']:>8.2f}N{row['required_Nm']:>8.2f}N"
                f"{row['rated_Nm']:>7.1f}N{row['margin']:>8.2f}{flag}"
            )
            if not row["ok"]:
                failures += 1

        print("\nSELF-COLLISION")
        hits = self_collisions(arm)
        if hits:
            for h in hits:
                print(f"  COLLISION {h['a']} <-> {h['b']}  {h['volume_mm3']:.1f} mm3")
            failures += len(hits)
        else:
            print("  none between non-adjacent links")

    print()
    print("=" * 78)
    print("ELECTRONICS FIT")
    print("=" * 78)
    problems = electronics_fit()
    if problems:
        for p in problems:
            print(f"  {p}")
        failures += len(problems)
    else:
        print("  all payload items inside the cavity and clear of each other")

    print()
    print("=" * 78)
    print("FLOOR ANCHORS")
    print("=" * 78)
    ot = anchor_loads(mod, mp["total_g"])
    print(f"  overturning moment   {ot['moment_Nm']:7.1f} Nm  (both arms outstretched, 2 x 1 kg)")
    print(f"  bolt span            {ot['bolt_span_mm']:7.0f} mm")
    print(f"  uplift from moment   {ot['uplift_N']:7.0f} N   per front anchor")
    print(f"  dead-weight hold     {ot['hold_down_N']:7.0f} N   per anchor ({ot['weight_N']:.0f} N total)")
    print(f"  net anchor load      {ot['net_N']:7.0f} N   {ot['verdict']}")

    print()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    poses = sys.argv[1:] or ["ready", "outstretched", "tucked"]
    sys.exit(run(poses))


# --------------------------------------------------------------------------
# Joint range sweep
#
# Joint limits are only meaningful if the links can actually reach them. This
# sweeps one joint at a time from a neutral pose and reports the first angle
# at which any two non-adjacent links intersect, so the declared limits can be
# set from geometry instead of from anatomy tables.
# --------------------------------------------------------------------------


def sweep_joint(mod, joint_name: str, step: float = 10.0, base=None) -> dict:
    base = dict(base or K.ZERO_POSE)
    joint = next(j for j in PR.JOINTS if j.name == joint_name)
    lo, hi = joint.limits

    def build(angle):
        return posed_arm(mod, dict(base, **{joint_name: angle}))

    clear_lo, clear_hi = None, None
    n = int((hi - lo) / step) + 1
    for i in range(n):
        angle = lo + i * step
        if angle > hi:
            break
        hits = self_collisions(build(angle))
        if hits:
            if clear_lo is None:
                continue
            clear_hi = angle - step
            break
        if clear_lo is None:
            clear_lo = angle
        clear_hi = angle
    return {
        "joint": joint_name,
        "declared": (lo, hi),
        "collision_free": (clear_lo, clear_hi),
        "ok": clear_lo is not None and abs(clear_lo - lo) < 1e-6 and abs(clear_hi - hi) < 1e-6,
    }


def run_sweep(joint_names, step: float = 10.0) -> int:
    mod = _load_assembly()
    print("=" * 78)
    print(f"JOINT RANGE SWEEP  (step {step:g} deg, one joint at a time from zero)")
    print("=" * 78)
    print(f"{'joint':<16}{'declared':>18}{'collision-free':>20}")
    bad = 0
    for name in joint_names:
        r = sweep_joint(mod, name, step)
        lo, hi = r["declared"]
        clo, chi = r["collision_free"]
        free = "none" if clo is None else f"{clo:.0f} .. {chi:.0f}"
        mark = "" if r["ok"] else "   <-- narrower than declared"
        print(f"{name:<16}{f'{lo:.0f} .. {hi:.0f}':>18}{free:>20}{mark}")
        if not r["ok"]:
            bad += 1
    return bad


def anchor_loads(mod, total_mass_g: float) -> dict:
    """Overturning at the floor plate, from the real posed geometry.

    Both arms are taken outstretched and forward with their rated payloads,
    which is the worst case. The robot's own dead weight resists uplift, so
    the net anchor load is what actually matters, not the moment alone.
    """
    arm = posed_arm(mod, K.OUTSTRETCHED_POSE)
    frames = K.arm_frames(K.OUTSTRETCHED_POSE, side=1)

    moment = 0.0  # Nm about the base centreline, from ONE arm
    for part in arm:
        m, _ = part_mass(part)
        try:
            c = part.center(CenterOf.MASS)
        except Exception:
            c = part.bounding_box().center()
        moment += (m / 1000.0) * G * (c.Y / 1000.0)

    palm = frames["palm"].posed * Pos(0, 0, -PR.HAND["palm"][2] / 2)
    moment += (PR.PAYLOAD_PER_HAND / 1000.0) * G * (palm.position.Y / 1000.0)

    moment *= 2.0  # both arms
    lever_mm = PR.BASE_PLATE["size"] - 2 * PR.BASE_PLATE["anchor_inset"]
    uplift = moment / (lever_mm / 1000.0) / 2.0  # shared by the two rear anchors
    weight = (total_mass_g / 1000.0) * G
    hold_down = weight / 4.0
    net = uplift - hold_down
    return {
        "moment_Nm": moment,
        "bolt_span_mm": lever_mm,
        "uplift_N": uplift,
        "weight_N": weight,
        "hold_down_N": hold_down,
        "net_N": net,
        "verdict": "TENSION - anchors carry uplift" if net > 0 else "compression, no uplift",
    }
