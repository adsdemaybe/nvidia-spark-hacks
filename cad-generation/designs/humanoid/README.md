# Stationary dual-arm humanoid torso

A fixed, floor-bolted robot: steel base plate → pedestal column → electronics
torso → two 7-DOF arms → three-finger hands with an opposable thumb. It is not
a rover and it does not drive; the base is anchored and the only things that
move are the fourteen arm joints and the digits.

Everything is parametric build123d. No dimension is typed into geometry twice:
link lengths, actuator envelopes, joint limits, wall thicknesses and the
electronics payload all live in `humanoid_params.py`, and the assembly places
every part by evaluating the forward kinematics rather than by hand-placing it.

## Files

| file | role |
|---|---|
| `humanoid_params.py` | Actuator catalogue, bearings, link geometry, joint limits, materials, electronics payload. Run it to print the sizing budget. |
| `humanoid_kinematics.py` | Forward kinematics, named poses, joint-limit enforcement, reach envelope. Run it to print fingertip positions. |
| `humanoid_parts.py` | Part builders. Each returns a closed solid in a documented local frame. |
| `humanoid_torso.step.py` | The assembly. `gen_step()` returns the labelled compound. |
| `check_parts.py` | Builds every part in isolation; catches boolean failures and disconnected solids. |
| `check_design.py` | Engineering checks against the real geometry: mass, joint torque, self-collision, electronics fit, anchor loads, joint-range sweep. |

## Coordinate convention

```
+X  robot right (the +X arm is the right arm)
+Y  robot forward
+Z  up, origin at the underside of the floor plate
```

The single rule that governs the arm: **a joint frame's origin is the output
face of that joint's actuator module, on the joint axis.** Each module's stator
therefore sits on the parent side of the frame and the driven link bolts to the
face at the origin. This is why the shoulder and elbow brackets are cranked
rather than straight — they have to reach around the module bodies.

## Kinematics

7 DOF per arm:

| joint | module | axis | limits (right arm) |
|---|---|---|---|
| shoulder_pitch | HDX-100 | X (lateral) | -60° … +180° |
| shoulder_roll | HDX-100 | Y (fore-aft) | -150° … +20° |
| shoulder_yaw | HDX-60 | Z (humeral) | -90° … +90° |
| elbow_pitch | HDX-80 | X | 0° … see limits note |
| wrist_yaw | HDX-40 | Z (forearm roll) | -90° … +90° |
| wrist_pitch | HDX-40 | X | -70° … +70° |
| wrist_roll | HDX-40 | Y (deviation) | -30° … +30° |

Negative shoulder roll is **abduction** (arm away from the torso); the small
positive range is adduction across the chest. The left arm is a true mirror
about the YZ plane, so its roll and yaw limits are negated.

## How the joints are built

Every joint is a strain-wave module with an integrated crossed-roller output
bearing, cantilevered the way modular arms (Franka, Kinova, UR) actually are —
not a pin in two plain bushings. Each interface is a pilot spigot plus a bolt
circle, so the module locates on the pilot and is clamped by the fasteners. The
hollow shaft through every module carries the loom, and each link's cavity is
open at both ends so the cable path is continuous from the torso to the hand.

Structural links are **shells**, not billets. The first mass pass built them
solid and the shoulder came out at 33 Nm static against a 40 Nm module; the
housings are now 6 mm walls around open cavities.

Two geometric rules fall out of the joints moving:

- A cranked link is trimmed to a **cylinder coaxial with the joint it hangs
  from** wherever it sits above that axis. A square shoulder there sweeps a
  larger circle as the joint rotates and fouls the parent.
- A tube feeding a pitch joint **stops short of the axis** by the driven link's
  swept radius, and the load path is carried the rest of the way by an outboard
  gusset. A tube running all the way down sits inside the driven link's sweep.

Both rules came from collision findings, not from theory.

## The hand

Three fingers plus an opposable thumb, two phalanges each, on 623 bearings.
Knuckles are **forks**: the fixed side carries two cheeks and the moving side's
boss runs between them. The palm shell is relieved around every boss, because
the boss is centred on the palm's own bottom face.

The finger drives are **not in the palm** — four actuators do not fit a
96 × 30 mm palm, and they do not fit the forearm either at full servo size.
They are four DSM-30 micro units in a 2 × 2 pack inside the forearm tube,
pulling tendons through the wrist; every phalanx carries a tendon channel and
the palm has a guide hole per digit. This is the Shadow-Hand arrangement and it
is what the fit checks forced.

## Electronics

The torso is a real enclosure, not a box with a label. The payload is declared
in `humanoid_params.PAYLOAD` and checked against the cavity and against itself:

```
battery_pack        250 x 90 x 72   12S4P 18650, 44.4 V
power_distribution  140 x 80 x 26   fused PDB, precharge, e-stop relay
dc_dc_converter     120 x 70 x 34   48 V -> 24/12/5 V
compute_module      110 x 110 x 32  Jetson-class carrier + SoM
motor_driver_left   170 x 100 x 22  8-channel FOC
motor_driver_right  170 x 100 x 22
```

Stacked bottom-up so the heaviest item is lowest. Every board gets a standoff
under each mounting hole, generated from its own mount list, so moving a board
in the parameters moves its hardware with it. The battery sits in a retaining
tray; a cable channel runs from the column bore up the rear wall to the
shoulders; the rear wall carries an 80 mm extraction fan on its real bolt
pattern and the side walls carry vent slots.

## Building and checking

```bash
V=../../.venv/bin/python
S=../../../.claude/skills/cad/scripts

$V humanoid_params.py             # actuator sizing budget
$V humanoid_kinematics.py         # fingertip positions and reach
$V check_parts.py                 # every part builds as one valid solid
CADGEN_WARM=1 $V $S/gen humanoid_torso.step.py --write
CADGEN_WARM=1 $V $S/inspect validate humanoid_torso.step.py
$V check_design.py                # mass, torque, collisions, fit, anchors
```

Change the exported pose by editing `POSE` in `humanoid_torso.step.py`; any of
`ready`, `zero`, `outstretched`, `abducted`, `tucked` from
`humanoid_kinematics.POSES` works, or pass a dict. An out-of-limit angle raises
`JointLimitError` rather than silently building an impossible robot.
