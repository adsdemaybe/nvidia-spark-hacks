"""MJCF for the demo_room scene.

Geometry mirrors tools/make_fixtures.py exactly -- same table extents, same
cube size, same bin walls, same placements -- so the physics twin and the GLB
visualisation describe one world rather than two that drift apart.

MJCF is Z-up right-handed in meters, which is already the STRUCT convention
(spec section 13B), so nothing is converted on the way in or out.

Ported from Andrew's independent `arxr-sim` package during the arvr/arxr
consolidation (see ../../../STATE.md) — unchanged except this docstring and
import paths elsewhere in the package. This is a SEPARATE placeholder robot
from `fixtures/robot/test_arm.urdf` (ar-datapipe's offline Pinocchio-IK
target) — they serve different pipeline stages (live physics-driven Twin
here vs. offline demonstration retargeting there) and were not unified;
flagged as a reasonable future cleanup, not urgent.
"""

from __future__ import annotations

# Keep in step with tools/make_fixtures.py and xr-web/src/scene.ts LAYOUT.
TABLE_POS = (0.4, 0.0, 0.0)
TABLE_SIZE = (1.20, 0.80, 0.75)
CUBE_SIZE = 0.06
CUBE_REST = (0.30, 0.10)
BIN_POS = (0.6, -0.7, 0.0)
BIN_SIZE = (0.30, 0.30, 0.25)
BIN_WALL = 0.02
# Placed so BOTH the cube on the table and the bin beside it are inside the
# workspace. The first placement put the bin 1.30 m away against ~0.95 m of
# reach, so the arm carried the cube partway and dropped it on the table.
ROBOT_BASE = (0.15, -0.70, 0.0)

ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3")

# Link lengths. The cube rests at (0.30, 0.10, 0.78) and the base is at
# (-0.5, 0, 0), so the arm needs ~0.81 m of horizontal reach plus margin --
# the first sizing gave 0.69 m and the arm simply never touched anything.
# (shoulder column, upper arm, forearm, wrist)
#
# The shoulder column must clear the table top (0.75 m). At 0.65 m the arm had
# to pass *through* the table to reach anything on it -- IK is collision-blind
# and happily returned those poses, then physics refused them and the arm
# thrashed. Approach from above instead.
LINK = (0.95, 0.45, 0.36, 0.16)


def build_mjcf(cube_height_m: float | None = None) -> str:
    """Return the scene as MJCF XML.

    `cube_height_m` lets a test drop the cube from a height; the default rests
    it on the table where the fixtures put it.
    """
    cz = cube_height_m if cube_height_m is not None else TABLE_SIZE[2] + CUBE_SIZE / 2

    tx, ty, _ = TABLE_POS
    hx, hy, hz = (TABLE_SIZE[0] / 2, TABLE_SIZE[1] / 2, TABLE_SIZE[2] / 2)

    bx, by, _ = BIN_POS
    bw, bd, bh = BIN_SIZE
    t = BIN_WALL

    l0, l1, l2, l3 = LINK

    return f"""<mujoco model="struct_demo_room">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>

  <default>
    <geom friction="0.9 0.02 0.001" solref="0.004 1" density="600"/>
    <joint damping="4.0" armature="0.05"/>
    <position kp="220" forcerange="-160 160"/>

    <!-- Arm links collide with the world but not with each other.
         contype=1/conaffinity=0 means (arm.contype & arm.conaffinity) == 0 both
         ways, while arm-vs-world still matches. Without this, a folded-back
         forearm rests against the upper arm and friction silently jams the pan
         joint: the actuator is commanded, reports no error, and the arm simply
         does not turn. -->
    <default class="arm">
      <geom contype="1" conaffinity="0"/>
    </default>
  </default>

  <worldbody>
    <light pos="0 0 3" dir="0 0 -1" diffuse="0.9 0.9 0.9"/>
    <geom name="floor" type="plane" size="6 6 0.1" rgba="0.2 0.22 0.25 1"/>

    <geom name="table" type="box" pos="{tx} {ty} {hz}" size="{hx} {hy} {hz}"
          rgba="0.55 0.45 0.33 1"/>

    <!-- Bin: floor plus four walls, matching the assembled GLB. -->
    <geom name="bin_floor" type="box" pos="{bx} {by} {t / 2}"
          size="{bw / 2} {bd / 2} {t / 2}" rgba="0.42 0.42 0.42 1"/>
    <geom name="bin_wall_px" type="box" pos="{bx + (bw - t) / 2} {by} {bh / 2}"
          size="{t / 2} {bd / 2} {bh / 2}" rgba="0.42 0.42 0.42 1"/>
    <geom name="bin_wall_nx" type="box" pos="{bx - (bw - t) / 2} {by} {bh / 2}"
          size="{t / 2} {bd / 2} {bh / 2}" rgba="0.42 0.42 0.42 1"/>
    <geom name="bin_wall_py" type="box" pos="{bx} {by + (bd - t) / 2} {bh / 2}"
          size="{bw / 2} {t / 2} {bh / 2}" rgba="0.42 0.42 0.42 1"/>
    <geom name="bin_wall_ny" type="box" pos="{bx} {by - (bd - t) / 2} {bh / 2}"
          size="{bw / 2} {t / 2} {bh / 2}" rgba="0.42 0.42 0.42 1"/>

    <body name="cube_01" pos="{CUBE_REST[0]} {CUBE_REST[1]} {cz}">
      <freejoint name="cube_free"/>
      <geom name="cube" type="box" size="{CUBE_SIZE / 2} {CUBE_SIZE / 2} {CUBE_SIZE / 2}"
            rgba="0.29 0.62 0.85 1" density="400"/>
    </body>

    <body name="robot_base" pos="{ROBOT_BASE[0]} {ROBOT_BASE[1]} {ROBOT_BASE[2]}">
      <geom class="arm" name="base" type="cylinder" size="0.15 0.05" pos="0 0 0.05"
            rgba="0.83 0.83 0.83 1"/>
      <body name="shoulder" pos="0 0 0.10">
        <joint name="shoulder_pan" type="hinge" axis="0 0 1" range="-3.14 3.14"/>
        <geom class="arm" name="shoulder_g" type="capsule" fromto="0 0 0 0 0 {l0}" size="0.05"
              rgba="0.78 0.78 0.80 1"/>
        <body name="upper_arm" pos="0 0 {l0}">
          <joint name="shoulder_lift" type="hinge" axis="0 1 0" range="-2.6 2.6"/>
          <geom class="arm" name="upper_g" type="capsule"
                fromto="0 0 0 {l1} 0 0" size="0.045"
                rgba="0.78 0.78 0.80 1"/>
          <body name="forearm" pos="{l1} 0 0">
            <joint name="elbow" type="hinge" axis="0 1 0" range="-2.8 2.8"/>
            <geom class="arm" name="forearm_g" type="capsule"
                  fromto="0 0 0 {l2} 0 0" size="0.038"
                  rgba="0.78 0.78 0.80 1"/>
            <body name="wrist" pos="{l2} 0 0">
              <joint name="wrist_1" type="hinge" axis="0 1 0" range="-3.14 3.14"/>
              <geom class="arm" name="wrist_g" type="capsule"
                    fromto="0 0 0 {l3} 0 0" size="0.030"
                    rgba="0.85 0.85 0.87 1"/>
              <body name="wrist_roll" pos="{l3} 0 0">
                <joint name="wrist_2" type="hinge" axis="1 0 0" range="-3.14 3.14"/>
                <!-- Every body carrying a joint needs mass, or the model will
                     not compile (mass must exceed mjMINVAL). -->
                <geom class="arm" name="wrist_roll_g" type="sphere" size="0.028"
                      rgba="0.85 0.85 0.87 1"/>
                <body name="tool" pos="0.03 0 0">
                  <joint name="wrist_3" type="hinge" axis="0 0 1" range="-3.14 3.14"/>
                  <geom class="arm" name="tool_g" type="box" size="0.03 0.02 0.02"
                        rgba="0.88 0.42 0.45 1"/>
                  <site name="end_effector" pos="0.03 0 0" size="0.012"/>
                </body>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>

  <!-- Grasp is modelled as a weld that is switched on when the tool is at the
       object, not as friction between finger pads. That is a simplification and
       should be described as one: it makes carrying reliable so the interesting
       physics (the drop into the bin, the settle) is what gets exercised. -->
  <equality>
    <weld name="grasp" body1="tool" body2="cube_01" active="false" solref="0.02 1"/>
  </equality>

  <actuator>
    <position name="a_shoulder_pan" joint="shoulder_pan"/>
    <position name="a_shoulder_lift" joint="shoulder_lift"/>
    <position name="a_elbow" joint="elbow"/>
    <position name="a_wrist_1" joint="wrist_1"/>
    <position name="a_wrist_2" joint="wrist_2"/>
    <position name="a_wrist_3" joint="wrist_3"/>
  </actuator>
</mujoco>
"""
