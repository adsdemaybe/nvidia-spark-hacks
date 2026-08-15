"""
Simulation export: turn the CAD model into a physics-ready articulation.

Emits, from the single source of truth in rover_arm.py:
    sim/rover.urdf        link/joint tree, real inertials, drive limits
    sim/rover.usda        native USD with UsdPhysics schemas for Isaac Sim
    sim/meshes/*.stl      visual meshes (millimetres; URDF applies scale)

WHY THIS FILE EXISTS
--------------------
build123d `Joint`s are a CAD-assembly construct. They are NOT written to STEP or
STL — both formats are geometry-only. Exporting the CAD and importing it into a
simulator yields disconnected rigid meshes with no articulation. The kinematics
have to be re-emitted in a format a physics engine understands, which is what
this module does.

MASS MODEL
----------
Printed parts are PLA at 1.24 g/cm3, integrated over the real solid via OCC.
The dominant masses, however, are the purchased components, which exist in the
CAD only as envelopes: 5x NEMA17 at 280 g, 2x servo at 60 g, a 170 g LiPo, and
the Pi. Those are added as point masses at their true mount positions and
combined with the solid's tensor through the parallel-axis theorem. Without them
the dynamics are meaningless — the motors outweigh the printed chassis 4:1.

COLLISION
---------
Convex hulls are wrong for this model: the chassis is a hollow tub, so its hull
fills solid and the electronics bay disappears. Collision is therefore an
explicit primitive decomposition (boxes and cylinders) per link — faster and
more stable in PhysX than a decomposed mesh, and exact for this geometry.

Run:  .venv-cad/bin/python export_sim.py
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
from build123d import export_stl
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

import rover_arm as R

SIM_DIR = "sim"
MESH_DIR = os.path.join(SIM_DIR, "meshes")
MM = 0.001                      # millimetre -> metre

# ---- Material densities (kg/mm^3) -------------------------------------------
RHO_PLA = 1.24e-6

# ---- Purchased component masses (kg), from the datasheets ------------------
M_NEMA17 = 0.280
M_SERVO = 0.060
M_LIPO = 0.170
M_RPI = 0.046
M_DRIVER = 0.0015
M_BEARING = 0.0081              # 626ZZ, SKF

# Component bounding boxes (mm) — needed for valid inertia, see PointMass.
D_NEMA17 = (R.NEMA_FRAME, R.NEMA_FRAME, R.NEMA_BODY_L)
D_SERVO = (R.SERVO_L, R.SERVO_W, R.SERVO_H)
D_LIPO = (R.BATT_L, R.BATT_W, R.BATT_H)
D_RPI = (R.RPI_L, R.RPI_W, R.RPI_MAX_H)
D_DRIVER = (R.DRV_L, R.DRV_W, R.DRV_H)
D_BEARING = (R.BRG_OD, R.BRG_OD, R.BRG_W)

# ---- Actuator limits --------------------------------------------------------
NEMA_TORQUE = 0.43              # N.m holding torque, 17HS4401
NEMA_VEL = 5.0                  # rad/s, conservative for open-loop stepping
SERVO_TORQUE = 2.1              # N.m stall @6.8V, DS3218
SERVO_VEL = 7.3                 # rad/s from 0.14 s/60deg
JAW_FORCE = 50.0                # N at the rack
JAW_VEL = 0.05                  # m/s

WHEEL_FRICTION = 1.1            # rubber-ish tread on hard ground


# =============================================================================
# Inertial computation
# =============================================================================

@dataclass
class PointMass:
    """
    A purchased component lumped onto a link.

    `dims` is mandatory and is NOT cosmetic: a dimensionless point mass has a
    rank-deficient inertia tensor, and combining several of them produces a
    tensor that violates the triangle inequality (A + B >= C) — physically
    impossible, and rejected outright by MuJoCo and PhysX. Each component
    therefore contributes a real solid-box tensor about its own centre.
    """
    name: str
    mass: float                       # kg
    pos: tuple[float, float, float]   # mm, in the link frame
    dims: tuple[float, float, float]  # mm, component bounding box


@dataclass
class Inertial:
    mass: float                 # kg
    com: np.ndarray             # m
    tensor: np.ndarray          # kg.m^2 about the COM


def _skew_term(m: float, r: np.ndarray) -> np.ndarray:
    """Parallel-axis contribution of a point mass m at r (mm)."""
    return m * (np.dot(r, r) * np.eye(3) - np.outer(r, r))


def solid_inertia(part, rho: float) -> tuple[float, np.ndarray, np.ndarray]:
    """
    Mass (kg), COM (mm) and inertia about the COM (kg.mm^2) of a solid.

    OCC's GProp_GProps.MatrixOfInertia() is referenced to the CENTRE OF MASS,
    not to the origin — verified against a 100mm cube offset 200mm, which
    returns V*(a^2+b^2)/12 rather than the origin-referenced value. Treating it
    as origin-referenced silently produces tensors with negative eigenvalues.
    """
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(part.wrapped, props)
    vol = props.Mass()                     # 'Mass' of a volume prop == volume
    c = props.CentreOfMass()
    com = np.array([c.X(), c.Y(), c.Z()])
    m = props.MatrixOfInertia()
    tensor = np.array([[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    return vol * rho, com, tensor * rho


def combine(parts, points: list[PointMass], rho: float = RHO_PLA) -> Inertial:
    """
    Combine solids and lumped component masses into one link inertial.

    Everything is accumulated about the link origin, then shifted to the
    combined centre of mass, which is what URDF and USD both expect.
    """
    total_m = 0.0
    weighted = np.zeros(3)
    I_origin = np.zeros((3, 3))

    for part, offset in parts:
        m, com, I_body = solid_inertia(part, rho)   # I_body is about its own COM
        c = com + np.asarray(offset, dtype=float)
        total_m += m
        weighted += m * c
        I_origin += I_body + _skew_term(m, c)       # parallel axis to the origin

    for p in points:
        r = np.asarray(p.pos, dtype=float)
        dx, dy, dz = p.dims
        # Solid-box tensor about the component's own centre, then parallel-axis.
        I_body = (p.mass / 12.0) * np.diag([dy * dy + dz * dz,
                                            dx * dx + dz * dz,
                                            dx * dx + dy * dy])
        total_m += p.mass
        weighted += p.mass * r
        I_origin += I_body + _skew_term(p.mass, r)

    com = weighted / total_m if total_m else np.zeros(3)
    I_com = I_origin - _skew_term(total_m, com)
    return Inertial(mass=total_m, com=com * MM, tensor=I_com * MM * MM)


# =============================================================================
# Link / joint model
# =============================================================================

@dataclass
class Collider:
    kind: str                              # "box" | "cylinder"
    dims: tuple                            # box: (x,y,z) mm; cyl: (radius, len)
    origin: tuple = (0.0, 0.0, 0.0)        # mm
    rpy: tuple = (0.0, 0.0, 0.0)


@dataclass
class Link:
    name: str
    solids: list = field(default_factory=list)     # [(part, offset_mm)]
    points: list = field(default_factory=list)     # [PointMass]
    colliders: list = field(default_factory=list)
    mesh: str | None = None
    mesh_origin: tuple = (0.0, 0.0, 0.0)


@dataclass
class Joint:
    name: str
    kind: str                              # revolute | continuous | prismatic | fixed
    parent: str
    child: str
    origin: tuple                          # mm, in the parent frame
    axis: tuple = (0.0, 0.0, 1.0)
    limit: tuple | None = None             # (lower, upper) rad or m
    effort: float = 0.0
    velocity: float = 0.0
    mimic: tuple | None = None             # (joint_name, multiplier)


def build_model() -> tuple[list[Link], list[Joint]]:
    """Assemble the simulation topology from the CAD parameters."""
    deck_z = R.CHASSIS_H                       # top of the lid
    pin_z = R.BASE_H + R.LINK_W * 0.62         # shoulder axis, turntable frame
    y_off = R.YOKE_GAP / 2.0 + R.LINK_T / 2.0
    span = R._yoke_span()
    axle_y = R.CHASSIS_W / 2.0 + R.WHEEL_W / 2.0 + R.PART_GAP
    grip_shift = (R.GRIP_BODY_L / 2.0 + R.LINK_W / 2.0, 0.0, -R.GRIP_BODY_H / 2.0)

    # --- base_link: chassis + bolted lid, merged (the lid never moves) -------
    bay = R.BAY
    base_points = [
        PointMass("yaw_motor", M_NEMA17,
                  (R.TURNTABLE_X, 0, deck_z - R.NEMA_BODY_L / 2.0), D_NEMA17),
        PointMass("battery", M_LIPO,
                  (bay.battery.position.X, bay.battery.position.Y,
                   bay.battery.position.Z + R.BATT_H / 2.0), D_LIPO),
        PointMass("rpi", M_RPI,
                  (bay.rpi.position.X, bay.rpi.position.Y, bay.rpi.position.Z),
                  D_RPI),
    ]
    for sy in (-1, 1):
        for ax in (-R.AXLE_X, R.AXLE_X):
            base_points.append(PointMass(
                "drive_motor", M_NEMA17,
                (ax, sy * (R.CHASSIS_W / 2.0 - R.SIDE_WALL - R.NEMA_BODY_L / 2.0),
                 R.AXLE_Z), D_NEMA17))
    for i, loc in enumerate(bay.drivers):
        p = loc.position
        base_points.append(PointMass(f"driver{i}", M_DRIVER, (p.X, p.Y, p.Z),
                                     D_DRIVER))
    if R.BALLAST_M > 0:
        # Rear counterweight: the arm is a long forward lever, so ballast at the
        # tail buys tip-over margin far more cheaply than lengthening the chassis.
        base_points.append(PointMass(
            "ballast", R.BALLAST_M,
            (-R.CHASSIS_L / 2.0 + R.SIDE_WALL + 15.0, 0, R.WALL + 15.0),
            (30.0, 60.0, 30.0)))

    hw = R.CHASSIS_W / 2.0 - R.SIDE_WALL / 2.0
    hl = R.CHASSIS_L / 2.0 - R.SIDE_WALL / 2.0
    base = Link(
        "base_link",
        solids=[(R.build_chassis(), (0, 0, 0)),
                (R.build_lid(), (0, 0, R.CHASSIS_H - R.LID_T))],
        points=base_points,
        colliders=[
            Collider("box", (R.CHASSIS_L, R.CHASSIS_W, R.WALL), (0, 0, R.WALL / 2)),
            Collider("box", (R.CHASSIS_L, R.SIDE_WALL, R.CHASSIS_H),
                     (0, -hw, R.CHASSIS_H / 2)),
            Collider("box", (R.CHASSIS_L, R.SIDE_WALL, R.CHASSIS_H),
                     (0, hw, R.CHASSIS_H / 2)),
            Collider("box", (R.SIDE_WALL, R.CHASSIS_W - 2 * R.SIDE_WALL, R.CHASSIS_H),
                     (-hl, 0, R.CHASSIS_H / 2)),
            Collider("box", (R.SIDE_WALL, R.CHASSIS_W - 2 * R.SIDE_WALL, R.CHASSIS_H),
                     (hl, 0, R.CHASSIS_H / 2)),
            Collider("box", (R.CHASSIS_L - 2 * R.SIDE_WALL,
                             R.CHASSIS_W - 2 * R.SIDE_WALL, R.LID_T),
                     (0, 0, R.CHASSIS_H - R.LID_T / 2)),
        ],
        mesh="base_link.stl")

    turntable = Link(
        "turntable",
        solids=[(R.build_turntable(), (0, 0, 0))],
        points=[PointMass(
            "shoulder_motor",
            R.MOTORS[R.SHOULDER_MOTOR]["mass"] + (0.168 if R.SHOULDER_GEAR > 1.5
                                                  else 0.0),
            (0, y_off + R.LINK_T / 2.0 + R.NEMA_BODY_L / 2.0, pin_z),
            (R.MOTORS[R.SHOULDER_MOTOR]["frame"],
             R.MOTORS[R.SHOULDER_MOTOR]["frame"],
             R.MOTORS[R.SHOULDER_MOTOR]["body_len"]))],
        colliders=[
            Collider("cylinder", (R.BASE_D / 2.0, R.BASE_H), (0, 0, R.BASE_H / 2)),
            Collider("box", (R.LINK_W, R.LINK_T, R.LINK_W * 1.15),
                     (0, -y_off, R.BASE_H + R.LINK_W * 1.15 / 2)),
            Collider("box", (R.LINK_W, R.LINK_T, R.LINK_W * 1.15),
                     (0, y_off, R.BASE_H + R.LINK_W * 1.15 / 2)),
        ],
        mesh="turntable.stl")

    def arm_link(name, length, servo):
        pts = [PointMass("elbow_servo", M_SERVO, (length, span, 0), D_SERVO)] if servo else []
        pts.append(PointMass("bearings", 2 * M_BEARING, (length, 0, 0), D_BEARING))
        return Link(
            name,
            solids=[(R.build_link(length, name, servo), (0, 0, 0))],
            points=pts,
            colliders=[
                Collider("box", (length, R.LINK_T, R.LINK_W), (length / 2, 0, 0)),
                Collider("box", (R.LINK_W * 1.5, 2 * span, R.LINK_W),
                         (length - R.LINK_W / 4, 0, 0)),
            ],
            mesh=f"{name}.stl")

    link1 = arm_link("link_shoulder", R.LINK1_LEN, True)
    link2 = arm_link("link_elbow", R.LINK2_LEN, False)

    gripper = Link(
        "gripper_body",
        solids=[(R.build_gripper_body(), grip_shift)],
        points=[PointMass("gripper_servo", M_SERVO, grip_shift, D_SERVO)],
        colliders=[Collider("box", (R.GRIP_BODY_L, R.GRIP_BODY_W, R.GRIP_BODY_H),
                            grip_shift)],
        mesh="gripper_body.stl", mesh_origin=grip_shift)

    jaws = [
        Link(f"jaw_{s}", solids=[(R.build_jaw(side=sd), (0, 0, 0))],
             colliders=[
                 Collider("box", (8 * math.pi * R.GEAR_MODULE, R.JAW_T, R.JAW_T)),
                 Collider("box", (R.JAW_T, R.JAW_T, R.JAW_LEN),
                          (8 * math.pi * R.GEAR_MODULE / 2 - R.JAW_T / 2,
                           -sd * R.RACK_OFFSET, R.JAW_LEN / 2)),
             ],
             mesh=f"jaw_{s}.stl")
        for s, sd in (("a", -1), ("b", 1))
    ]

    wheels = []
    for i, (sy, ax) in enumerate([(sy, ax) for sy in (-1, 1)
                                  for ax in (-R.AXLE_X, R.AXLE_X)]):
        wheels.append(Link(
            f"wheel_{i}",
            solids=[(R.build_wheel(), (0, 0, 0))],
            colliders=[Collider("cylinder", (R.WHEEL_D / 2.0, R.WHEEL_W),
                                rpy=(math.pi / 2, 0, 0))],
            mesh="wheel.stl"))

    links = [base, turntable, link1, link2, gripper] + jaws + wheels

    # --- Joints -----------------------------------------------------------
    joints = [
        Joint("yaw", "revolute", "base_link", "turntable",
              (R.TURNTABLE_X, 0, deck_z), (0, 0, 1),
              (-math.pi, math.pi), NEMA_TORQUE, NEMA_VEL),
        Joint("shoulder", "revolute", "turntable", "link_shoulder",
              (0, 0, pin_z), (0, 1, 0),
              (math.radians(R.SHOULDER_RANGE[0]), math.radians(R.SHOULDER_RANGE[1])),
              NEMA_TORQUE, NEMA_VEL),
        Joint("elbow", "revolute", "link_shoulder", "link_elbow",
              (R.LINK1_LEN, 0, 0), (0, 1, 0),
              (math.radians(R.ELBOW_RANGE[0]), math.radians(R.ELBOW_RANGE[1])),
              SERVO_TORQUE, SERVO_VEL),
        Joint("wrist", "fixed", "link_elbow", "gripper_body",
              (R.LINK2_LEN, 0, 0)),
        Joint("jaw_a", "prismatic", "gripper_body", "jaw_a",
              (R.GRIP_BODY_L / 2.0 + R.LINK_W / 2.0, -R.RACK_OFFSET, 0),
              (-1, 0, 0), (0.0, R.RACK_TRAVEL / 2.0 * MM), JAW_FORCE, JAW_VEL),
        Joint("jaw_b", "prismatic", "gripper_body", "jaw_b",
              (R.GRIP_BODY_L / 2.0 + R.LINK_W / 2.0, R.RACK_OFFSET, 0),
              (1, 0, 0), (0.0, R.RACK_TRAVEL / 2.0 * MM), JAW_FORCE, JAW_VEL,
              mimic=("jaw_a", 1.0)),
    ]
    for i, (sy, ax) in enumerate([(sy, ax) for sy in (-1, 1)
                                  for ax in (-R.AXLE_X, R.AXLE_X)]):
        joints.append(Joint(f"axle_{i}", "continuous", "base_link", f"wheel_{i}",
                            (ax, sy * axle_y, R.AXLE_Z), (0, 1, 0),
                            None, NEMA_TORQUE, 20.0))
    return links, joints


# =============================================================================
# URDF
# =============================================================================

def _xyz(v) -> str:
    return " ".join(f"{c * MM:.6g}" for c in v)


def write_srdf(joints: list[Joint], path: str) -> None:
    """
    Self-collision filtering, as a proper SRDF.

    `disable_collisions` is an SRDF element — putting it in the URDF is invalid
    and silently ignored. Running clearances here are 0.4-1.0 mm, so adjacent
    pairs contact-jitter without this.
    """
    root = ET.Element("robot", name="rover_arm")
    for j in joints:
        ET.SubElement(root, "disable_collisions", link1=j.parent,
                      link2=j.child, reason="Adjacent")
    ET.SubElement(root, "disable_collisions", link1="jaw_a", link2="jaw_b",
                  reason="Coupled")
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_urdf(links: list[Link], joints: list[Joint], path: str,
               visuals: bool = True) -> None:
    """`visuals=False` omits mesh references — used by the search loop, whose
    physics tests run on the primitive colliders and never need the meshes."""
    robot = ET.Element("robot", name="rover_arm")

    for lk in links:
        el = ET.SubElement(robot, "link", name=lk.name)
        inert = combine(lk.solids, lk.points)

        i = ET.SubElement(el, "inertial")
        ET.SubElement(i, "origin", xyz=" ".join(f"{c:.6g}" for c in inert.com),
                      rpy="0 0 0")
        ET.SubElement(i, "mass", value=f"{inert.mass:.6g}")
        t = inert.tensor
        ET.SubElement(i, "inertia",
                      ixx=f"{t[0,0]:.6g}", ixy=f"{t[0,1]:.6g}", ixz=f"{t[0,2]:.6g}",
                      iyy=f"{t[1,1]:.6g}", iyz=f"{t[1,2]:.6g}", izz=f"{t[2,2]:.6g}")

        if lk.mesh and visuals:
            v = ET.SubElement(el, "visual")
            ET.SubElement(v, "origin", xyz=_xyz(lk.mesh_origin), rpy="0 0 0")
            g = ET.SubElement(v, "geometry")
            ET.SubElement(g, "mesh", filename=f"meshes/{lk.mesh}",
                          scale=f"{MM} {MM} {MM}")

        for c in lk.colliders:
            col = ET.SubElement(el, "collision")
            ET.SubElement(col, "origin", xyz=_xyz(c.origin),
                          rpy=" ".join(f"{a:.6g}" for a in c.rpy))
            g = ET.SubElement(col, "geometry")
            if c.kind == "box":
                ET.SubElement(g, "box", size=_xyz(c.dims))
            else:
                ET.SubElement(g, "cylinder", radius=f"{c.dims[0] * MM:.6g}",
                              length=f"{c.dims[1] * MM:.6g}")

    for j in joints:
        el = ET.SubElement(robot, "joint", name=j.name, type=j.kind)
        ET.SubElement(el, "parent", link=j.parent)
        ET.SubElement(el, "child", link=j.child)
        ET.SubElement(el, "origin", xyz=_xyz(j.origin), rpy="0 0 0")
        if j.kind != "fixed":
            ET.SubElement(el, "axis", xyz=" ".join(f"{a:.6g}" for a in j.axis))
            attrs = {"effort": f"{j.effort:.6g}", "velocity": f"{j.velocity:.6g}"}
            if j.limit:
                attrs |= {"lower": f"{j.limit[0]:.6g}", "upper": f"{j.limit[1]:.6g}"}
            if j.kind != "continuous":
                ET.SubElement(el, "limit", **attrs)
            else:
                ET.SubElement(el, "limit", **attrs)
            ET.SubElement(el, "dynamics", damping="0.05", friction="0.01")
        if j.mimic:
            ET.SubElement(el, "mimic", joint=j.mimic[0],
                          multiplier=f"{j.mimic[1]:.6g}", offset="0")

    ET.indent(robot, space="  ")
    ET.ElementTree(robot).write(path, encoding="utf-8", xml_declaration=True)


# =============================================================================
# USD (Isaac Sim)
# =============================================================================

def write_usd(links: list[Link], joints: list[Joint], path: str) -> None:
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Sdf, Gf

    stage = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    root = UsdGeom.Xform.Define(stage, "/rover_arm")
    stage.SetDefaultPrim(root.GetPrim())
    UsdPhysics.ArticulationRootAPI.Apply(root.GetPrim())

    # Wheel contact material.
    mat = UsdShade.Material.Define(stage, "/rover_arm/materials/wheel")
    mp = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
    mp.CreateStaticFrictionAttr(WHEEL_FRICTION)
    mp.CreateDynamicFrictionAttr(WHEEL_FRICTION * 0.9)
    mp.CreateRestitutionAttr(0.05)

    prim_path = {}
    for lk in links:
        p = f"/rover_arm/{lk.name}"
        prim_path[lk.name] = p
        xf = UsdGeom.Xform.Define(stage, p)
        UsdPhysics.RigidBodyAPI.Apply(xf.GetPrim())

        inert = combine(lk.solids, lk.points)
        mass_api = UsdPhysics.MassAPI.Apply(xf.GetPrim())
        mass_api.CreateMassAttr(float(inert.mass))
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*[float(c) for c in inert.com]))
        # USD wants a diagonalised tensor plus the principal-axes rotation.
        evals, evecs = np.linalg.eigh(inert.tensor)
        if np.linalg.det(evecs) < 0:
            evecs[:, 0] *= -1
        mass_api.CreateDiagonalInertiaAttr(
            Gf.Vec3f(*[float(max(v, 1e-9)) for v in evals]))
        q = _mat_to_quat(evecs)
        mass_api.CreatePrincipalAxesAttr(Gf.Quatf(q[0], Gf.Vec3f(q[1], q[2], q[3])))

        for n, c in enumerate(lk.colliders):
            cp = f"{p}/collision_{n}"
            if c.kind == "box":
                g = UsdGeom.Cube.Define(stage, cp)
                g.CreateSizeAttr(1.0)
                UsdGeom.Xformable(g).AddScaleOp().Set(
                    Gf.Vec3f(*[float(d * MM) for d in c.dims]))
            else:
                g = UsdGeom.Cylinder.Define(stage, cp)
                g.CreateRadiusAttr(float(c.dims[0] * MM))
                g.CreateHeightAttr(float(c.dims[1] * MM))
                g.CreateAxisAttr("Y" if abs(c.rpy[0]) > 0.1 else "Z")
            UsdGeom.Xformable(g).AddTranslateOp().Set(
                Gf.Vec3d(*[float(o * MM) for o in c.origin]))
            UsdPhysics.CollisionAPI.Apply(g.GetPrim())
            if lk.name.startswith("wheel"):
                UsdShade.MaterialBindingAPI(g.GetPrim()).Bind(
                    mat, materialPurpose="physics")

    for j in joints:
        jp = f"/rover_arm/joints/{j.name}"
        if j.kind == "fixed":
            uj = UsdPhysics.FixedJoint.Define(stage, jp)
        elif j.kind == "prismatic":
            uj = UsdPhysics.PrismaticJoint.Define(stage, jp)
        else:
            uj = UsdPhysics.RevoluteJoint.Define(stage, jp)

        uj.CreateBody0Rel().SetTargets([prim_path[j.parent]])
        uj.CreateBody1Rel().SetTargets([prim_path[j.child]])
        uj.CreateLocalPos0Attr(Gf.Vec3f(*[float(o * MM) for o in j.origin]))
        uj.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))

        if j.kind != "fixed":
            axis = "X" if abs(j.axis[0]) else ("Y" if abs(j.axis[1]) else "Z")
            uj.CreateAxisAttr(axis)
            if j.limit and j.kind != "continuous":
                if j.kind == "prismatic":
                    uj.CreateLowerLimitAttr(float(j.limit[0]))
                    uj.CreateUpperLimitAttr(float(j.limit[1]))
                else:
                    uj.CreateLowerLimitAttr(float(math.degrees(j.limit[0])))
                    uj.CreateUpperLimitAttr(float(math.degrees(j.limit[1])))
            drive_type = "linear" if j.kind == "prismatic" else "angular"
            drive = UsdPhysics.DriveAPI.Apply(uj.GetPrim(), drive_type)
            drive.CreateTypeAttr("force")
            drive.CreateMaxForceAttr(float(j.effort))
            drive.CreateStiffnessAttr(2000.0 if j.kind != "continuous" else 0.0)
            drive.CreateDampingAttr(80.0)
            if j.mimic:
                # Isaac's URDF importer handles <mimic> poorly, so record the
                # coupling explicitly for the USD consumer to enforce.
                uj.GetPrim().CreateAttribute(
                    "physics:mimicJoint", Sdf.ValueTypeNames.String).Set(j.mimic[0])
                uj.GetPrim().CreateAttribute(
                    "physics:mimicMultiplier",
                    Sdf.ValueTypeNames.Float).Set(float(j.mimic[1]))

    stage.GetRootLayer().Save()


def _mat_to_quat(m: np.ndarray):
    """Rotation matrix -> (w, x, y, z)."""
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    return float(w), float(x), float(y), float(z)


# =============================================================================
# Meshes
# =============================================================================

def write_meshes(links: list[Link]) -> None:
    os.makedirs(MESH_DIR, exist_ok=True)
    done = set()
    for lk in links:
        if not lk.mesh or lk.mesh in done:
            continue
        done.add(lk.mesh)
        if lk.name == "base_link":
            from build123d import Compound
            part = Compound(children=[
                s.moved(__import__("build123d").Location(o)) if any(o) else s
                for s, o in lk.solids])
        else:
            part = lk.solids[0][0]
        export_stl(part, os.path.join(MESH_DIR, lk.mesh))
        print(f"  meshes/{lk.mesh}")


if __name__ == "__main__":
    os.makedirs(SIM_DIR, exist_ok=True)
    links, joints = build_model()

    print("meshes:")
    write_meshes(links)

    urdf = os.path.join(SIM_DIR, "rover.urdf")
    write_urdf(links, joints, urdf)
    print(f"wrote {urdf}")

    srdf = os.path.join(SIM_DIR, "rover.srdf")
    write_srdf(joints, srdf)
    print(f"wrote {srdf}")

    usd = os.path.join(SIM_DIR, "rover.usda")
    if os.path.exists(usd):
        os.remove(usd)
    write_usd(links, joints, usd)
    print(f"wrote {usd}")

    print("\nlink inertials:")
    total = 0.0
    for lk in links:
        it = combine(lk.solids, lk.points)
        total += it.mass
        print(f"  {lk.name:15} {it.mass * 1000:7.1f} g  "
              f"com=({it.com[0] * 1000:6.1f},{it.com[1] * 1000:6.1f},"
              f"{it.com[2] * 1000:6.1f}) mm  "
              f"Ixx={it.tensor[0, 0]:.3e}")
    print(f"  {'TOTAL':15} {total * 1000:7.1f} g")
