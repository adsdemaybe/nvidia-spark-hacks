"""Emit a MuJoCo model of the rocket with real thruster actuators.

Every thruster is a site-mounted force actuator pointing along its true
geometric direction, so driving the sliders exercises the SAME control-authority
matrix the harness scores. Cant the nozzles to zero and the roll sliders stop
working, in the viewer, for the same reason the criterion fails.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rocket as K

MM = 0.001


def build(path="sim/rocket/rocket.xml", design=None):
    if design:
        K.reconfigure(**{k: v for k, v in design.items()
                         if k in K.DESIGN_VARS or k in K.DISCRETE_VARS})
    # Compute the real inertia tensor from the solid. An earlier version
    # freehanded diaginertia as mass*0.0012, which understated roll inertia by
    # ~2.6x and made the vehicle spin implausibly fast in the viewer. This
    # project's own rule: never freehand a number you can compute.
    import numpy as np
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    solid = K.build_rocket()
    props = GProp_GProps()
    for part in (solid.solids() if hasattr(solid, "solids") else [solid]):
        pr = GProp_GProps()
        BRepGProp.VolumeProperties_s(part.wrapped, pr)
        props.Add(pr)
    vol = props.Mass()
    rho_eff = K.total_mass() / vol            # scale printed density to real mass
    mi = props.MatrixOfInertia()
    I = np.array([[mi.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    I = I * rho_eff * MM * MM                 # -> kg.m^2, about the COM
    # eigvalsh returns ASCENDING. The rocket's long axis is Z, so the SMALLEST
    # principal inertia is roll and must land in Izz; the two large ones are
    # pitch/yaw. Writing max() into all three slots (an earlier bug here) makes
    # the vehicle refuse to roll.
    a, b, c = sorted(float(max(v, 1e-9)) for v in np.linalg.eigvalsh(I))
    i_pitch, i_yaw, i_roll = b, c, a

    mass = K.total_mass()
    cg = K.centre_of_mass() * MM
    thrust = K.MAIN_MOTORS[K.MAIN_MOTOR]["avg_thrust"]
    spawn = 0.6

    sites, acts = [], []
    # Main thruster: fires DOWN out of the base, pushing the vehicle up.
    sites.append('      <site name="main" pos="0 0 0" zaxis="0 0 1" size="0.012"/>')
    acts.append(f'    <general site="main" gear="0 0 1 0 0 0" '
                f'ctrlrange="0 {thrust:.1f}" name="main_thruster"/>')

    for i, (pos, direction, ang) in enumerate(K.rcs_geometry()):
        p = pos * MM
        d = direction
        sites.append(f'      <site name="rcs{i}" pos="{p[0]:.5f} {p[1]:.5f} {p[2]:.5f}" '
                     f'zaxis="{d[0]:.5f} {d[1]:.5f} {d[2]:.5f}" size="0.006"/>')
        acts.append(f'    <general site="rcs{i}" gear="0 0 1 0 0 0" '
                    f'ctrlrange="0 {K.RCS_THRUST:.1f}" name="rcs{i}"/>')

    xml = f'''<mujoco model="rocket_ship">
  <compiler angle="radian" meshdir="." balanceinertia="true" discardvisual="false"/>
  <option gravity="0 0 -9.81" density="1.225" viscosity="1.8e-5" integrator="implicitfast"/>
  <visual><global offwidth="1600" offheight="1200"/></visual>

  <asset>
    <mesh name="ship" file="rocket_ship.stl" scale="{MM} {MM} {MM}"/>
    <texture name="grid" type="2d" builtin="checker" rgb1=".14 .16 .19"
             rgb2=".19 .22 .26" width="512" height="512"/>
    <material name="grid" texture="grid" texrepeat="8 8" reflectance="0.05"/>
    <material name="hull" rgba="0.72 0.75 0.79 1"/>
  </asset>

  <worldbody>
    <light pos="2 -2 4" dir="-0.4 0.4 -1" directional="true"/>
    <geom name="ground" type="plane" size="30 30 0.1" material="grid"/>
    <body name="rocket" pos="0 0 {spawn}">
      <freejoint name="root"/>
      <inertial pos="0 0 {cg:.5f}" mass="{mass:.4f}"
                diaginertia="{i_pitch:.6f} {i_yaw:.6f} {i_roll:.6f}"/>
      <geom type="mesh" mesh="ship" material="hull" mass="0"
            friction="0.6 0.01 0.001"/>
{chr(10).join(sites)}
    </body>
  </worldbody>

  <actuator>
{chr(10).join(acts)}
  </actuator>
</mujoco>
'''
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(xml)
    return path, mass, thrust, (i_pitch, i_yaw, i_roll)


if __name__ == "__main__":
    d = json.load(open("export/rocket/design_ship.json"))
    p, m, t, I = build(design=d)
    print(f"wrote {p}   mass {m:.3f} kg   main thrust {t:.0f} N   "
          f"RCS 4 x {K.RCS_THRUST:.1f} N")
    print(f"  computed inertia Ixx Iyy Izz (kg.m^2): {I[0]:.5f} {I[1]:.5f} {I[2]:.5f}")
    print(f"  freehand guess had been:               {m*0.045:.5f} {m*0.045:.5f} {m*0.0012:.5f}")
    print(f"  roll inertia was understated {I[2]/(m*0.0012):.1f}x")
