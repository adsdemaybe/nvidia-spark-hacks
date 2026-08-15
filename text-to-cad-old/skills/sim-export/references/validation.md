# Simulation Validation

Run in order; stop and fix at the first failing step.

1. **Parse** — load the URDF with a strict parser. Confirms the link/joint tree,
   the single root, and that every joint's parent and child resolve.
2. **Inertia audit** — every tensor positive-definite and `A + B >= C`. This is
   the step that catches point-mass and reference-frame bugs, and it catches
   them before the physics engine produces a confusing error.
3. **Physics load with a floating base** — compile in MuJoCo via `MjSpec`, add a
   free joint to the root and a ground plane. Without the free joint the root is
   welded to the world and the base mass is silently dropped from the model, so
   a mass total that looks wrong by exactly the base link is this bug.
4. **Settle** — drop the robot and integrate. It must land upright, stop moving,
   and produce no NaN.
5. **Drive** — apply wheel torque and confirm the base translates.
6. **Consumer smoke test** — Isaac Sim, Gazebo, or RViz load, when available.

Report which steps ran and which were skipped. A skipped step is not a pass.

## What still is not proven

- Joint **axis direction**. A sign error passes every check above. Only a viewer
  sweep of each joint against a written expectation catches it.
- Dynamic torque, thermal derating, deflection, terrain.
- USD correctness without Isaac Sim installed.
