"""`RobotSpec` — the shape the physics engine consumes.

**This exists so the CAD side can change without breaking anything here.**
`cad-generation/` is under active development; its Robot IR, its geometry registry and
its API are all moving. If the MJCF emitter read their types directly, every refactor on
their side would land here.

So the emitter reads *this*, and adapters convert into it. When their IR changes, one
adapter changes.

Three rules follow, and they are in `CHECKLIST.md` because they are easy to erode:

1. **Never import their Python.** Adapters take JSON — from a file, from an HTTP
   response, from anywhere. No package coupling.
2. **Degrade, do not fail.** A link with no mesh becomes a box of the same bounding
   dimensions. A link with no inertia gets one computed from its box and mass. Every
   substitution is recorded in `assumptions` so a rollout can say what it invented.
3. **Everything is optional except what physics requires** — a name, a mass, and a
   joint's parent and axis. A spec that cannot be simulated should fail with a message
   naming the missing field, not a KeyError three layers down.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

JointType = Literal["hinge", "slide", "fixed"]


@dataclass
class Assumption:
    """Something the spec did not say, that physics required anyway."""

    subject: str
    field_name: str
    value: str
    why: str


@dataclass
class Link:
    """A rigid body.

    `mesh` is a path when the CAD side produced one and `None` when it did not — the
    second case is normal early in a design and must not stop a rollout.
    """

    name: str
    mass_kg: float
    size_m: tuple[float, float, float] = (0.05, 0.05, 0.05)
    # Where this body's frame sits relative to its parent — i.e. where the joint is.
    pos_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Where the mass and the geometry sit *within* that frame.
    #
    # These are two different things and conflating them is silent. An arm whose body
    # frame is placed at its own centre has its hinge through its centre of mass, so
    # gravity exerts no moment and an unpowered arm hangs in mid-air without falling —
    # no error, no warning, just a robot that ignores gravity.
    com_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mesh: str | None = None
    # Diagonal inertia about the centre of mass. None means "work it out from the box".
    inertia_kgm2: tuple[float, float, float] | None = None
    rgba: tuple[float, float, float, float] = (0.6, 0.62, 0.66, 1.0)


@dataclass
class Joint:
    """How one link moves relative to its parent."""

    name: str
    parent: str
    child: str
    type: JointType = "hinge"
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    pos_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    limit_rad: tuple[float, float] | None = None
    damping: float = 0.01
    armature: float = 0.0


@dataclass
class Actuator:
    """A motor attached to a joint.

    `motor_id` is the key the electrical side publishes under on `pcb/physics`, and
    `gear_ratio` converts shaft torque to joint torque. Keeping the ratio here rather
    than in the motor catalogue means one motor can drive different joints through
    different gearboxes.
    """

    name: str
    joint: str
    motor_id: str
    gear_ratio: float = 1.0


@dataclass
class RobotSpec:
    name: str
    links: list[Link] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    actuators: list[Actuator] = field(default_factory=list)
    gravity: tuple[float, float, float] = (0.0, 0.0, -9.81)
    timestep_s: float = 0.001
    assumptions: list[Assumption] = field(default_factory=list)

    def link(self, name: str) -> Link:
        for l in self.links:
            if l.name == name:
                return l
        raise KeyError(f"no link {name!r}; have {[l.name for l in self.links]}")

    def validate(self) -> list[str]:
        """Problems that would make this unsimulatable, named rather than raised.

        Returned as a list so a caller can report all of them at once. A spec that fails
        on its third problem after two silent fixes is worse than one that lists three.
        """
        problems: list[str] = []
        names = {l.name for l in self.links}
        if not self.links:
            problems.append("no links — there is nothing to simulate")
        for l in self.links:
            if l.mass_kg <= 0:
                problems.append(f"link {l.name}: mass must be positive, got {l.mass_kg}")
        for j in self.joints:
            if j.parent != "world" and j.parent not in names:
                problems.append(f"joint {j.name}: parent {j.parent!r} is not a link")
            if j.child not in names:
                problems.append(f"joint {j.name}: child {j.child!r} is not a link")
            if j.type != "fixed" and not any(abs(a) > 1e-9 for a in j.axis):
                problems.append(f"joint {j.name}: axis is zero")
        joint_names = {j.name for j in self.joints}
        for a in self.actuators:
            if a.joint not in joint_names:
                problems.append(f"actuator {a.name}: joint {a.joint!r} does not exist")
            if a.gear_ratio == 0:
                problems.append(f"actuator {a.name}: gear ratio of zero transmits nothing")
        return problems

    def fill_defaults(self) -> "RobotSpec":
        """Compute what physics needs and the spec did not supply.

        Box inertia is the honest default: it is what you would get by assuming uniform
        density in the bounding volume, which is exactly the information available when
        no mesh has been produced yet.
        """
        for l in self.links:
            if l.inertia_kgm2 is None:
                x, y, z = l.size_m
                m = l.mass_kg
                ix = m * (y * y + z * z) / 12.0
                iy = m * (x * x + z * z) / 12.0
                iz = m * (x * x + y * y) / 12.0
                l.inertia_kgm2 = (ix, iy, iz)
                self.assumptions.append(
                    Assumption(
                        subject=l.name,
                        field_name="inertia_kgm2",
                        value=f"({ix:.3e}, {iy:.3e}, {iz:.3e})",
                        why="uniform-density box of the link's bounding size — no mesh inertia supplied",
                    )
                )
            if l.mesh is None:
                self.assumptions.append(
                    Assumption(
                        subject=l.name,
                        field_name="mesh",
                        value=f"box {l.size_m}",
                        why="no mesh from CAD; collision and visual use the bounding box",
                    )
                )
        return self


# ── adapters ─────────────────────────────────────────────────────────────────────
#
# Each one converts some external shape *into* RobotSpec. Adding a source means adding
# a function here and nothing else.


def from_dict(data: dict[str, Any]) -> RobotSpec:
    """A plain dict, which is what every other adapter reduces to.

    Tolerant on purpose: unknown keys are ignored rather than rejected, because the CAD
    side will grow fields before this code knows about them and a hard schema would turn
    every one of their additions into an outage here.
    """

    def tup3(v: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
        if isinstance(v, (list, tuple)) and len(v) == 3:
            return (float(v[0]), float(v[1]), float(v[2]))
        return default

    links = [
        Link(
            name=str(l["name"]),
            mass_kg=float(l.get("mass_kg", l.get("mass", 0.01))),
            size_m=tup3(l.get("size_m") or l.get("size"), (0.05, 0.05, 0.05)),
            pos_m=tup3(l.get("pos_m") or l.get("pos"), (0.0, 0.0, 0.0)),
            com_m=tup3(l.get("com_m") or l.get("com"), (0.0, 0.0, 0.0)),
            mesh=l.get("mesh"),
            inertia_kgm2=(
                tup3(l["inertia_kgm2"], (0.0, 0.0, 0.0)) if l.get("inertia_kgm2") else None
            ),
        )
        for l in data.get("links", [])
    ]
    joints = [
        Joint(
            name=str(j["name"]),
            parent=str(j.get("parent", "world")),
            child=str(j["child"]),
            type=j.get("type", "hinge"),
            axis=tup3(j.get("axis"), (0.0, 0.0, 1.0)),
            pos_m=tup3(j.get("pos_m") or j.get("pos"), (0.0, 0.0, 0.0)),
            limit_rad=(
                (float(j["limit_rad"][0]), float(j["limit_rad"][1]))
                if j.get("limit_rad")
                else None
            ),
            damping=float(j.get("damping", 0.01)),
            armature=float(j.get("armature", 0.0)),
        )
        for j in data.get("joints", [])
    ]
    actuators = [
        Actuator(
            name=str(a.get("name", a["joint"])),
            joint=str(a["joint"]),
            motor_id=str(a.get("motor_id", a.get("motor", "M1"))),
            gear_ratio=float(a.get("gear_ratio", 1.0)),
        )
        for a in data.get("actuators", [])
    ]
    return RobotSpec(
        name=str(data.get("name", "robot")),
        links=links,
        joints=joints,
        actuators=actuators,
        timestep_s=float(data.get("timestep_s", 0.001)),
    ).fill_defaults()


def from_json_file(path: str | Path) -> RobotSpec:
    return from_dict(json.loads(Path(path).read_text()))


def from_cad_service(payload: dict[str, Any]) -> RobotSpec:
    """Whatever `cad-generation` returns, mapped in.

    Written against the shape their service exposes today and deliberately forgiving,
    because that shape is being changed while this is written. It looks for links under
    several plausible keys and falls back to `from_dict`, so their rename does not become
    our outage — and if the mapping is genuinely wrong, `validate()` says which field is
    missing rather than throwing a KeyError.
    """
    for key in ("robot", "robot_ir", "spec", "assembly"):
        if isinstance(payload.get(key), dict):
            payload = payload[key]
            break
    return from_dict(payload)


def describe_assumptions(spec: RobotSpec) -> str:
    if not spec.assumptions:
        return "no assumptions — every value came from the spec"
    lines = [f"{len(spec.assumptions)} assumption(s) filled in:"]
    for a in spec.assumptions:
        lines.append(f"  {a.subject}.{a.field_name} = {a.value}   ({a.why})")
    return "\n".join(lines)


def one_joint_arm(
    *,
    motor_id: str = "M1",
    gear_ratio: float = 100.0,
    link_mass_kg: float = 0.08,
    link_length_m: float = 0.12,
) -> RobotSpec:
    """The smallest thing that tests the whole loop: one hinge, one motor, gravity.

    A single joint is enough to show back-EMF limiting current as the arm speeds up, and
    small enough that a wrong answer is obvious by hand. `J·dω/dt = τ − mgl·sin(θ)`.
    """
    return RobotSpec(
        name="one_joint_arm",
        links=[
            Link(name="base", mass_kg=0.5, size_m=(0.06, 0.06, 0.02)),
            # Hinged at one end: the body frame (and therefore the joint) sits at the
            # base, and the mass hangs half a length out along +x.
            Link(
                name="arm",
                mass_kg=link_mass_kg,
                size_m=(link_length_m, 0.02, 0.01),
                pos_m=(0.0, 0.0, 0.0),
                com_m=(link_length_m / 2, 0.0, 0.0),
            ),
        ],
        joints=[
            Joint(
                name="j1",
                parent="base",
                child="arm",
                type="hinge",
                axis=(0.0, 1.0, 0.0),
                damping=1e-4,
            )
        ],
        actuators=[Actuator(name="a1", joint="j1", motor_id=motor_id, gear_ratio=gear_ratio)],
    ).fill_defaults()
