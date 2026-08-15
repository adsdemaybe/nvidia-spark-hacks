"""Emit a MuJoCo model of the 12-DOF quadruped: 4 legs x (abduct, hip, knee).

Masses are assigned per body from the real printed volume PLUS the motor that
body carries, because the motors dominate — 12 of them against a printed frame.
Letting MuJoCo infer mass from mesh volume alone would understate the machine by
roughly 4x and make every dynamic result meaningless.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quadruped as Q
from build123d import export_stl

MM = 0.001
MESH_DIR = "sim/quad/meshes"

# Joint travel. INFERRED — mechanical hard stops are not modelled in the CAD.
ABDUCT_RANGE = (-0.6, 0.6)
HIP_RANGE = (-1.8, 1.8)
KNEE_RANGE = (-2.7, 0.0)


def write_meshes():
    os.makedirs(MESH_DIR, exist_ok=True)
    parts = {
        "trunk": Q.build_body(),
        "hip": Q.build_hip_bracket(),
        "upper": Q.build_leg_segment(Q.UPPER_LEG, "upper"),
        "lower": Q.build_leg_segment(Q.LOWER_LEG, "lower"),
    }
    vols = {}
    for name, p in parts.items():
        export_stl(p, f"{MESH_DIR}/{name}.stl", tolerance=0.3, angular_tolerance=0.5)
        vols[name] = p.volume * Q.RHO_PLA
    return vols


def build(path="sim/quad/quad.xml", design=None):
    if design:
        Q.reconfigure(**{k: v for k, v in design.items()
                         if k in Q.DESIGN_VARS or k in Q.DISCRETE_VARS})
    vols = write_meshes()

    m_abduct = Q.MOTORS[Q.ABDUCT_MOTOR]["mass"]
    m_hip = Q.MOTORS[Q.HIP_MOTOR]["mass"]
    m_knee = Q.MOTORS[Q.KNEE_MOTOR]["mass"]

    # Which body carries which motor: the abduction motors bolt to the trunk,
    # the hip motor rides on the hip bracket, the knee motor on the upper leg.
    mass_trunk = vols["trunk"] + 4 * m_abduct + 0.35      # +avionics/battery
    mass_hip = vols["hip"] + m_hip
    mass_upper = vols["upper"] + m_knee
    mass_lower = vols["lower"]

    sol = Q.stance_angles(Q.STANCE_H, 0.0)
    hip_a, knee_a = sol if sol else (0.6, -1.2)
    spawn = (Q.STANCE_H + Q.BODY_H / 2.0) * MM + 0.02

    legs, acts, qpos = [], [], []
    for (hx, hy), name in zip(Q.hip_positions(), Q.LEGS):
        side = 1.0 if hy > 0 else -1.0
        yaw = 0 if side > 0 else math.pi          # mirror the bracket
        legs.append(f'''
      <body name="hip_{name}" pos="{hx*MM:.5f} {hy*MM:.5f} 0" euler="0 0 {yaw:.5f}">
        <joint name="abduct_{name}" type="hinge" axis="1 0 0"
               range="{ABDUCT_RANGE[0]} {ABDUCT_RANGE[1]}" damping="0.35"/>
        <geom type="mesh" mesh="hip" mass="{mass_hip:.4f}" material="link"/>
        <body name="upper_{name}" pos="0 {Q.ABDUCT_OFF*MM:.5f} 0">
          <joint name="hip_{name}" type="hinge" axis="0 1 0"
                 range="{HIP_RANGE[0]} {HIP_RANGE[1]}" damping="0.35"/>
          <geom type="mesh" mesh="upper" mass="{mass_upper:.4f}" material="link"/>
          <body name="lower_{name}" pos="0 0 {-Q.UPPER_LEG*MM:.5f}">
            <joint name="knee_{name}" type="hinge" axis="0 1 0"
                   range="{KNEE_RANGE[0]} {KNEE_RANGE[1]}" damping="0.35"/>
            <geom type="mesh" mesh="lower" mass="{mass_lower:.4f}" material="link"/>
            <geom name="foot_{name}" type="sphere" size="{Q.FOOT_R*MM:.4f}"
                  pos="0 0 {-Q.LOWER_LEG*MM:.5f}" mass="0.02"
                  friction="1.2 0.02 0.001" material="foot"/>
          </body>
        </body>
      </body>''')
        for j, lo, hi, kp in (("abduct", *ABDUCT_RANGE, 40),
                              ("hip", *HIP_RANGE, 90),
                              ("knee", *KNEE_RANGE, 90)):
            acts.append(f'    <position name="{j}_{name}" joint="{j}_{name}" '
                        f'kp="{kp}" ctrlrange="{lo} {hi}" forcerange="-25 25"/>')
        qpos += [0.0, hip_a, knee_a]

    key = " ".join(f"{v:.4f}" for v in
                   [0, 0, spawn, 1, 0, 0, 0] + qpos)
    ctrl = " ".join(f"{v:.4f}" for v in qpos)

    xml = f'''<mujoco model="quadruped">
  <compiler angle="radian" meshdir="meshes" autolimits="true"/>
  <option gravity="0 0 -9.81" timestep="0.002" integrator="implicitfast"/>
  <visual><global offwidth="1600" offheight="1200"/></visual>

  <asset>
    <mesh name="trunk" file="trunk.stl" scale="{MM} {MM} {MM}"/>
    <mesh name="hip"   file="hip.stl"   scale="{MM} {MM} {MM}"/>
    <mesh name="upper" file="upper.stl" scale="{MM} {MM} {MM}"/>
    <mesh name="lower" file="lower.stl" scale="{MM} {MM} {MM}"/>
    <texture name="grid" type="2d" builtin="checker" rgb1=".13 .15 .18"
             rgb2=".18 .21 .25" width="512" height="512"/>
    <material name="grid" texture="grid" texrepeat="10 10" reflectance="0.05"/>
    <material name="body" rgba="0.78 0.62 0.36 1"/>
    <material name="link" rgba="0.62 0.66 0.72 1"/>
    <material name="foot" rgba="0.20 0.22 0.25 1"/>
  </asset>

  <worldbody>
    <light pos="1.5 -1.5 3" dir="-0.3 0.3 -1" directional="true"/>
    <geom name="ground" type="plane" size="30 30 0.1" material="grid"
          friction="1.2 0.02 0.001"/>
    <body name="trunk" pos="0 0 {spawn:.4f}">
      <freejoint name="root"/>
      <geom type="mesh" mesh="trunk" mass="{mass_trunk:.4f}" material="body"/>{"".join(legs)}
    </body>
  </worldbody>

  <actuator>
{chr(10).join(acts)}
  </actuator>

  <keyframe>
    <key name="stand" qpos="{key}" ctrl="{ctrl}"/>
  </keyframe>
</mujoco>
'''
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(xml)
    total = mass_trunk + 4 * (mass_hip + mass_upper + mass_lower + 0.02)
    return path, total, math.degrees(hip_a), math.degrees(knee_a)


if __name__ == "__main__":
    d = None
    if os.path.exists("/tmp/quad_design.json"):
        d = json.load(open("/tmp/quad_design.json"))
    p, m, h, k = build(design=d)
    print(f"wrote {p}")
    print(f"  total mass {m:.2f} kg   stance: hip {h:.0f} deg, knee {k:.0f} deg")
