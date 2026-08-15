# test_arm.urdf — placeholder, NOT the real deployment robot

This is a made-up 6-DOF revolute serial arm used only to exercise the
retarget (Pinocchio IK) and verify (MuJoCo replay) pipeline shape while no
real robot URDF exists in this repo yet. Base at origin in `struct_world` /
`robot_base`, shoulder height 0.15m, upper-arm + forearm 0.4m each, small
wrist offsets — max horizontal reach ~0.95m, chosen so the fixture episode
positions in `../ar-xr/sample_episode.jsonl` (roughly x:0.1-0.6, y:-0.2-0.3,
z:0.5-0.55) are comfortably reachable.

Loaded by both Pinocchio (`pin.buildModelFromUrdf`) and MuJoCo
(`mujoco.MjModel.from_xml_path`, which supports URDF directly) — one file,
so IK and replay can never silently disagree about the kinematic chain.

Delete this and point `ar_datapipe.robot_model` at the real URDF once F3 or
the hardware team produces one.
