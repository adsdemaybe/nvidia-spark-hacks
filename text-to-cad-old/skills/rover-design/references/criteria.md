# Success Criteria

Every criterion is measured by `scripts/evaluate`. A design passes only when all
of them pass simultaneously. They are defined in
`packages/roverkit/design_loop.py`; this file explains what each one means and
what actually moves it.

| Criterion | Measures | Fails when |
| --- | --- | --- |
| `cad_builds` | Every part is a valid B-rep solid | A boolean produced an invalid shape |
| `bay_clearance` | The real electronics BOM fits with margin | A board overlaps a wall, another board, or the lid |
| `mechanics` | Joint, bearing, and pin fits | Fork gap, pin span, or bearing seat is inconsistent |
| `inertia_valid` | Every tensor positive-definite and `A + B >= C` | A mass model bug, not a design problem |
| `sim_loads` | MuJoCo compiles the articulation with a floating base | Invalid inertials, broken tree, bad joint |
| `settles` | Dropped on a plane it lands upright and stops | Tips over, sinks, or explodes numerically |
| `drives` | Wheel torque translates the base | Insufficient traction or torque |
| `arm_holds` | Static gravity torque at the shoulder vs motor torque | Actuator undersized for the arm |
| `backlash` | Gearbox play multiplied by reach | End-effector slop exceeds the limit |
| `payload` | Tip-over payload at full forward reach | Below the target mass |

## Diagnosis by magnitude

Compare `value` against `target` in the report:

- **Within ~20%** — a tuning problem. A continuous variable will reach it.
- **1.5x to 3x** — usually part selection. `arm_holds` off by 1.9x is not
  fixable by shrinking the arm without destroying its capability.
- **Beyond 3x** — topology. The design needs a new mechanism, a counterweight,
  a different drivetrain, or a criterion that was wrong to begin with.

## Coupling

These are not independent, and the couplings are where naive optimisation goes
wrong:

- A bigger motor fixes `arm_holds` but adds mass high on the arm, which hurts
  `payload` and can hurt `settles`.
- Shrinking the chassis improves tip-over geometry per unit mass but eventually
  breaks `bay_clearance`, which cannot be negotiated — the electronics are a
  fixed physical BOM.
- A gearbox fixes `arm_holds` cheaply but introduces `backlash`, which is why
  that criterion exists at all.
- Shortening the arm improves `payload` and `arm_holds` simultaneously and is
  almost always the lazy answer. It trades away the robot's reason to exist.

## What is NOT measured

Stated so nobody mistakes a passing report for a working robot:

- Dynamic torque under acceleration — only static holding torque is checked.
- Thermal derating. A stepper holding static load heats and loses torque.
- Compliance, deflection, and structural stress. Links are rigid bodies here.
- Terrain, slopes, and obstacles. The settle test uses a flat plane.
- Cost, lead time, and manufacturability.
- Wiring routing, connector access, and service access.
