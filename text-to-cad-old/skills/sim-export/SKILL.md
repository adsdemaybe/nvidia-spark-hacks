---
name: sim-export
description: Emit and validate simulation artifacts from a parametric CAD model — URDF, SRDF, and USD with UsdPhysics schemas for Isaac Sim, MuJoCo, Gazebo or PyBullet. Use when exporting a robot to a physics engine, computing link inertials and centres of mass, building collision geometry, setting joint drive limits, or debugging a robot that will not load, sinks through the floor, explodes numerically, or has invalid inertia. Use the $rover-design skill to change the design itself; this skill only exports what the CAD already describes.
---

# Sim Export

Provenance: maintained in this workspace under `skills/sim-export/`.

Use this skill to turn a CAD model into a physics-ready articulation. The
correctness risks are unit scale, inertia reference frames, collision convexity,
and the silent loss of kinematics on export.

## Core Rules

1. **CAD formats carry no kinematics.** STEP and STL are geometry only. Joints
   authored in a CAD assembly do not survive export — a simulator sees
   disconnected rigid meshes. The articulation must be re-emitted here.
2. **Units are metres.** The CAD is millimetres. A missed conversion produces a
   210-metre rover that appears to work until nothing else makes sense.
3. **Never hand-write an inertia tensor.** Compute it from the solid. Verify
   every tensor is positive-definite and satisfies `A + B >= C`; MuJoCo and
   PhysX both reject tensors that do not, and a dimensionless point mass
   produces exactly such a tensor. See `references/inertials.md`.
4. **Lump the purchased components.** Motors, battery and boards dominate the
   mass and exist in CAD only as envelopes. Omitting them makes the dynamics
   meaningless — here the motors outweigh the printed chassis roughly 4:1.
5. **Do not convex-hull a hollow body.** The chassis is a tub; its hull fills
   solid and the electronics bay disappears. Use an explicit primitive
   decomposition. See `references/collision.md`.
6. `disable_collisions` is **SRDF, not URDF**. Placing it in a URDF is invalid
   and silently ignored.
7. **A mobile robot needs a floating base.** A URDF root is welded to the world
   by MuJoCo's importer, which also silently drops the base link's mass.
8. Validate with `scripts/validate` before reporting completion, and report
   which checks ran and which were skipped.

## Design Handoff

This skill exports; it does not decide. If a criterion fails because the design
is wrong — an undersized actuator, an unstable wheelbase — hand back to
`$rover-design` rather than adjusting an exported number in place. Editing the
URDF directly desynchronises it from the CAD, and the next export silently
reverts the edit.

## Workflow

1. Confirm the design is current. If `sim/design.json` disagrees with the loaded
   model, re-export rather than trusting the artifacts on disk.
2. Emit with `scripts/export`: meshes, URDF, SRDF, and USD.
3. Validate with `scripts/validate`, which parses the URDF, checks the link and
   joint tree, verifies every inertia tensor, and loads the model in a physics
   engine with a floating base.
4. Run a settle and drive smoke test — a model can parse cleanly and still sink,
   tip, or refuse to move.
5. Report which validation steps ran, which were skipped, and why.

## Commands

```bash
python scripts/export
python scripts/export --no-meshes           # skip tessellation; faster for loops
python scripts/validate sim/rover.urdf
python scripts/validate sim/rover.urdf --format json
```

`scripts/validate` exits nonzero if any check fails.

## Known Limits

Validation is a guardrail, not proof the robot works. Verified here: parse, tree
topology, inertia validity, physics load, settle, and drive. Not verified:
dynamic torque under acceleration, thermal derating, structural deflection,
terrain, or whether any joint axis points the direction a human intended. A
wrong axis sign passes every check in this skill.

USD generation cannot be verified without Isaac Sim installed. When Isaac is
absent, say the USD was emitted but not loaded, rather than implying it works.
