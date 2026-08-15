"""`RobotSpec` → MJCF, and a thin wrapper for stepping it.

MuJoCo is the physics engine because F3 already writes MJCF and settles scenes with it
(`realsim/packages/envgen/src/envgen/cousins/mjcf.py`), so the project has working
knowledge rather than a new dependency to learn.

The emitter is deliberately dumb: it reads `RobotSpec` and writes XML. Every decision
about what a link weighs, where a joint sits or which motor drives it was made upstream,
by the CAD side or by an adapter. That separation is what lets the CAD pipeline change
without touching this file.

**Torque is applied directly, not through MuJoCo's actuator dynamics.** MuJoCo can model
a motor itself — `<motor gear=...>` with a control signal — and using it here would mean
two motor models disagreeing about the same shaft. The electrical side owns the motor;
MuJoCo receives a torque in newton-metres and integrates the mechanics. One model, one
place.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from cosim.robot import RobotSpec


def _v3(v: tuple[float, float, float]) -> str:
    return " ".join(f"{x:.9g}" for x in v)


def to_mjcf(spec: RobotSpec) -> str:
    """Emit MJCF. Raises with every problem at once if the spec cannot be simulated."""
    problems = spec.validate()
    if problems:
        raise ValueError(
            "RobotSpec cannot be simulated:\n" + "\n".join(f"  - {p}" for p in problems)
        )

    root = ET.Element("mujoco", model=spec.name)
    ET.SubElement(
        root,
        "option",
        timestep=f"{spec.timestep_s:.9g}",
        gravity=_v3(spec.gravity),
        integrator="implicitfast",
    )
    ET.SubElement(root, "compiler", angle="radian", autolimits="true")

    worldbody = ET.SubElement(root, "worldbody")
    ET.SubElement(
        worldbody,
        "light",
        pos="0 0 1.5",
        dir="0 0 -1",
        directional="true",
    )

    # Children of each parent, so the body tree can be written recursively. A link with
    # no joint naming it as a child is attached to the world.
    children: dict[str, list[str]] = {}
    joint_for_child: dict[str, object] = {}
    for j in spec.joints:
        children.setdefault(j.parent, []).append(j.child)
        joint_for_child[j.child] = j
    attached = set(joint_for_child)
    roots = [l.name for l in spec.links if l.name not in attached]

    def write_body(parent_el: ET.Element, link_name: str) -> None:
        link = spec.link(link_name)
        body = ET.SubElement(parent_el, "body", name=link.name, pos=_v3(link.pos_m))

        j = joint_for_child.get(link.name)
        if j is not None and getattr(j, "type", "fixed") != "fixed":
            attrs = {
                "name": j.name,
                "type": j.type,
                "axis": _v3(j.axis),
                "pos": _v3(j.pos_m),
                "damping": f"{j.damping:.9g}",
            }
            if j.armature:
                attrs["armature"] = f"{j.armature:.9g}"
            if j.limit_rad is not None:
                attrs["range"] = f"{j.limit_rad[0]:.9g} {j.limit_rad[1]:.9g}"
            ET.SubElement(body, "joint", **attrs)

        ix, iy, iz = link.inertia_kgm2 or (1e-6, 1e-6, 1e-6)
        # The centre of mass sits at com_m within the body frame, which is what gives a
        # hinge at one end of a link the moment arm that makes gravity act on it.
        ET.SubElement(
            body,
            "inertial",
            pos=_v3(link.com_m),
            mass=f"{link.mass_kg:.9g}",
            diaginertia=f"{ix:.9g} {iy:.9g} {iz:.9g}",
        )

        # A mesh when CAD produced one, a box of the same bounding size when it did not.
        # The box is not a placeholder to be replaced later — for mass and inertia it is
        # exactly as good, and it keeps the loop runnable while the mesh pipeline moves.
        if link.mesh:
            ET.SubElement(
                body,
                "geom",
                type="mesh",
                mesh=f"{link.name}_mesh",
                pos=_v3(link.com_m),
                rgba=_v3(link.rgba[:3]) + f" {link.rgba[3]:.9g}",
            )
        else:
            half = tuple(s / 2 for s in link.size_m)
            ET.SubElement(
                body,
                "geom",
                type="box",
                size=_v3(half),  # type: ignore[arg-type]
                pos=_v3(link.com_m),
                rgba=_v3(link.rgba[:3]) + f" {link.rgba[3]:.9g}",  # type: ignore[index]
            )

        for child in children.get(link.name, []):
            write_body(body, child)

    for r in roots:
        write_body(worldbody, r)

    # Links joined by a joint must not collide with each other.
    #
    # MuJoCo happily generates contacts between a parent and its child, and for a robot
    # they are always touching by construction — a hinge holds two bodies at the same
    # point. The symptom is not a crash: the joint acquires a mystery resistance, and
    # a larger applied torque digs the geoms deeper into each other and moves *less*.
    # That is what 10x the torque producing a thousandth of the velocity turned out to be.
    #
    # Excluding only the adjacent pairs, rather than disabling collision globally, keeps
    # a real arm able to hit itself — which is a thing worth simulating.
    if spec.joints:
        contact = ET.SubElement(root, "contact")
        for j in spec.joints:
            if j.parent != "world":
                ET.SubElement(contact, "exclude", body1=j.parent, body2=j.child)

    if any(l.mesh for l in spec.links):
        asset = ET.SubElement(root, "asset")
        for l in spec.links:
            if l.mesh:
                ET.SubElement(asset, "mesh", name=f"{l.name}_mesh", file=l.mesh)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


class Mechanics:
    """A stepped MuJoCo model with torque in and shaft state out.

    Deliberately narrow: apply torque, step, read back. Anything richer belongs to
    whoever is running the simulation, not to the wrapper.
    """

    def __init__(self, spec: RobotSpec, xml: str | None = None):
        import mujoco  # imported here so the package works without the mech extra

        self._mj = mujoco
        self.spec = spec
        self.xml = xml if xml is not None else to_mjcf(spec)
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)

        # Joint ids by name, resolved once. A missing joint here means the emitter and
        # the spec disagree, which is a bug rather than a runtime condition.
        self._joint_id = {
            j.name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, j.name)
            for j in spec.joints
            if j.type != "fixed"
        }
        missing = [n for n, i in self._joint_id.items() if i < 0]
        if missing:
            raise RuntimeError(f"MJCF is missing joints the spec declares: {missing}")
        self._dof = {n: self.model.jnt_dofadr[i] for n, i in self._joint_id.items()}
        self._qpos = {n: self.model.jnt_qposadr[i] for n, i in self._joint_id.items()}
        self.actuator_by_motor = {a.motor_id: a for a in spec.actuators}

    def apply_motor_torque(self, motor_id: str, shaft_torque_nm: float) -> None:
        """Apply a motor's *shaft* torque to whatever joint it drives.

        The gear ratio is applied here, so callers pass what the electrical side
        computed and never have to know the mechanism.
        """
        act = self.actuator_by_motor.get(motor_id)
        if act is None:
            return
        self.data.qfrc_applied[self._dof[act.joint]] = shaft_torque_nm * act.gear_ratio

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self._mj.mj_step(self.model, self.data)

    def joint_state(self, joint: str) -> tuple[float, float]:
        """(angle rad, velocity rad/s) at the joint — i.e. after the gearbox."""
        return (
            float(self.data.qpos[self._qpos[joint]]),
            float(self.data.qvel[self._dof[joint]]),
        )

    def motor_shaft_speed(self, motor_id: str) -> float:
        """Shaft ω, which is joint ω multiplied by the gear ratio.

        This is the number the electrical side needs for back-EMF, and getting the
        direction of the ratio wrong here would silently scale the coupling — the joint
        turns slowly, the motor turns fast.
        """
        act = self.actuator_by_motor.get(motor_id)
        if act is None:
            return 0.0
        _, vel = self.joint_state(act.joint)
        return vel * act.gear_ratio

    def reset(self) -> None:
        self._mj.mj_resetData(self.model, self.data)

    @property
    def time(self) -> float:
        return float(self.data.time)


def write_mjcf(spec: RobotSpec, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_mjcf(spec))
    return p
