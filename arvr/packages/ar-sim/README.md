# ar-sim — Sky — live physics behind the Twin stream

Ported from Andrew's independent `arxr-sim` package during the arvr/arxr
consolidation (see `../../STATE.md`). MuJoCo rigid-body physics: gravity,
contact, a weld-based grasp, and a `PickAndPlaceDirector` that drives a
fixed set of Cartesian waypoints through `solve_ik`. Task success
(`TwinState.task.status == "success"`) is read off the cube's actual
position relative to the bin, never asserted by the script.

Wired into `ar_backend`'s `WS /twin/{scene_id}` as the "live" twin source
— see `ar_backend/twin.py`. Not the authoritative twin; OpenUSD in Isaac
Sim is, once that's wired (spec sections 52-53). This exists to make the
Twin/Replay demo honest on a machine with neither a Spark nor a Mac.

## Two IK implementations, on purpose

This package's `ik.py` (MuJoCo-Jacobian damped least-squares) and
`ar_datapipe.retarget` (Pinocchio CLIK) are **not duplicates** — they solve
different problems:

| | `ar_sim.ik` | `ar_datapipe.retarget` |
|---|---|---|
| Drives | a live simulated pick-place loop for the Twin visualization | an offline recorded TEACH demonstration |
| Engine | MuJoCo (same engine already stepping the physics) | Pinocchio |
| Orientation | position-only | full 6-DOF pose |
| Output feeds | `TwinState` over the twin WS route | `VerificationResult` + LeRobot export |

They were not unified during the consolidation — flagged as a reasonable
future cleanup once one of them targets a real robot URDF, not urgent for
the demo.

## Platform note

Same as `ar-datapipe`: `mujoco` is gated to `sys_platform == 'linux'` in
this package's `pyproject.toml`, and the module-level import is wrapped in
`try/except` so `import ar_sim` still succeeds on Windows — only
`MujocoTwinSource()`/`solve_ik()` actually need Linux.
