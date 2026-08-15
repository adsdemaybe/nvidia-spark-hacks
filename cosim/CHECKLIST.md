# Electromechanical co-simulation — build checklist

Plan: `../electromechanical-cosim-plan.md`. This is the execution list; tick items here
and record what was measured, not what was intended.

---

## Ground rule: the CAD side is moving under us

`cad-generation/` is being improved right now, by another session, while this is built.
Anything here that touches it must survive that. Three rules, and they are not
negotiable because breaking them means this work has to be redone when their side lands:

1. **Never import their Python.** No `from cad_api import …`, no `from engine import …`.
   Talk over HTTP (`/cad/…`) or over files. An import couples us to their package layout,
   their dependencies and their refactors all at once.
2. **Consume a shape, not a schema version.** The MJCF/URDF emitter takes a plain
   `RobotSpec` — links, joints, masses, meshes — and adapters convert *into* it. When
   their Robot IR changes, one adapter changes; the emitter, the bus and the physics do
   not.
3. **Degrade rather than fail.** A missing mesh becomes a primitive of the same bounding
   box and mass. A missing inertia becomes one computed from the box. Every substitution
   is labelled `ASSUMED` and reported. A pipeline that stops because CAD is mid-refactor
   is a pipeline nobody can develop against.

---

## M1 — transient behaviour engine ✅ done 2026-08-15

- [x] Motor model with `Kt ≡ Ke`, catalogue of three spanning the DRV8833 rating
- [x] Transient deck: supply impedance, bridge on-resistance, trace, winding, back-EMF
- [x] Freewheel path (the bug that made switching unphysical)
- [x] `.meas` windowed statistics, span sized to the slower time constant
- [x] Validated by sweeps: current tracks duty; current falls with ω via back-EMF
- [x] Stall case demonstrates the gate: 775-class draws 4.65 A into a 1.5 A channel

## M2 — the wire ✅ done 2026-08-15  (13 tests green)

- [x] `bus.py` — XPUB/XSUB proxy, publisher and subscriber helpers
- [x] `schema.py` — Pydantic models for every topic in plan §3.2
- [x] `clock.py` — the barrier: no participant advances past period *n* until all have
      published for *n*. **Without this the loop free-runs and the physics is nonsense.**
- [x] Malformed messages dropped **and counted** — a silent drop is how co-simulations lie
- [ ] `record.py` — every message to disk, so a failure can be inspected without re-running
- [x] Loopback test: two fake participants, barrier holds, counts reconcile

## M3 — mechanics ✅ done 2026-08-15

- [x] `robot.py` — `RobotSpec`: links (mass, inertia, mesh **or** primitive), joints
      (parent, child, axis, type, limits, damping), actuators (which joint, which motor)
- [x] Adapters *into* `RobotSpec`: a plain JSON file first, `cad-generation` second
- [x] `mjcf.py` — `RobotSpec` → MJCF, with degradation for missing meshes/inertia
- [x] MuJoCo installs and steps on aarch64 — **3.11.0, manylinux aarch64 wheel, verified**
- [x] One joint driven by a constant torque — θ ≈ ½ωt to 5%, gear ratio scales to 2%

### Two bugs M3 found, both silent

- **Adjacent links collided.** A hinge holds parent and child at the same point, so
  MuJoCo generated contacts between them. Not a crash — the joint acquired a mystery
  resistance, and *more* applied torque dug the geoms deeper and moved *less*. Ten times
  the torque gave a thousandth of the velocity. Fixed by excluding adjacent pairs only,
  so a real arm can still hit itself.
- **The hinge ran through the centre of mass.** `pos_m` was doing double duty as both
  the body frame and the mass position, which put the joint at the arm's middle. Gravity
  exerted no moment and an unpowered arm hung in mid-air — no error, just a robot that
  ignored gravity. `com_m` is now separate from `pos_m`.

Both produced plausible output. Neither would have been caught by a test that only
checked "does it run".

## M4 — close the loop ✅ done 2026-08-15

- [x] Electrical participant publishes torque from duty + ω (`electrical.py`, `rollout.py`)
- [x] Mechanical participant applies torque, publishes ω back
- [x] Back-EMF limits current — and **reverses it past no-load**, see the bug below
- [x] **Stability boundary measured**, not assumed — table below
- [x] Wall-clock: **123x real time** (0.4 s simulated in ~3 ms of loop, surface mode)
- [x] `surface` validated against `direct`: **6.09% of full scale** worst, 9x9 grid

### The rollout, and why it is right

Full duty into a 100:1 arm: starts at stall (1.510 A, 8.31 mN·m), spins up, **overshoots
no-load slightly, current goes negative and brakes**, then settles oscillating around
1345 rad/s — which is this motor's no-load speed and its physical ceiling. The
oscillation is the arm swinging under gravity, alternately helping and opposing.

### Stability — the risk the plan named, measured

| control period | outcome |
|---|---|
| 0.5 – 10 ms | stable |
| 20 ms | unbounded, ω = −7314 rad/s |
| 50 ms | **DIVERGED**, guard fired at −1.05e5 rad/s |

Explicit coupling is stable while the exchange period is short against both time
constants, and stops being stable somewhere between 10 and 20 ms for this arm. The guard
reports divergence rather than smoothing it, which is the whole point: a diverged rollout
is a failure of the *simulation*, not of the design, and conflating those would blame a
board for a solver problem.

### The bug that mattered most

`Math.abs()` on the measured current, justified in a comment as tidying up a reference
direction. **It was not a convention — the sign is the physics.** Above no-load the
back-EMF exceeds the supply, current genuinely reverses, and the motor brakes. Taking the
magnitude turned braking into driving, so the simulated motor accelerated past its own
no-load speed forever: 4179 rad/s on a motor whose ceiling is 1345.

Measured directly to settle it: i(VEMF) is **+1.513 A** driving at ω = 0 and **−1.299 A**
at ω = 2500. The convention was already correct and the code was throwing it away.

The lesson is narrower than "check your signs": a comment that explains why something
does not matter is worth more scrutiny than one that explains why it does. This one
argued its way past a real effect.

### A metric that was also wrong

Interpolation error was first judged per-point, giving 886%. That number is meaningless:
at 6% duty this drive makes 0.021 mN·m, so a 0.12 mN·m error is 592% relative and
irrelevant on a motor producing 8.31 mN·m. Judged against **full scale** it is 6.09%,
which is the figure that tells you whether a trajectory is affected. Both are reported —
the relative one still shows *where* the surface is soft, near the conduction threshold
where the freewheel diode puts a kink no grid density removes.

### The performance decision M4 forces

Spawning ngspice per control period costs ~20–50 ms of process startup before it solves
anything. A 1000-period rollout would spend a minute in `fork`. The transport is not the
bottleneck and never was.

So the electrical participant gets two modes, and the second is the default:

- **`direct`** — one ngspice run per period. Exact, slow, and the *reference*.
- **`surface`** — characterise the drive once over a (duty × ω) grid, then interpolate at
  run time. The board does not change during a rollout, so re-solving the same circuit
  thousands of times is wasted work.

**`surface` must be validated against `direct`**, on the same points, with the error
reported. A fast model nobody checked against the slow one is a guess.

## M5 — the gate

- [ ] Task predicate: joint reaches commanded pose within tolerance and deadline
- [ ] Electrical survival: peak current inside driver and trace rating for the rollout
- [ ] Thermal: duty-cycle dissipation, not the DC number
- [ ] Provenance: a rollout on `ASSUMED` constants is reported as such
- [ ] Verified failing: a motor too big, a trace too thin, a deadline too short

## M6 — three-way negotiation

- [ ] Rollout verdict feeds the §6 loop
- [ ] Failure routing by the deterministic discriminator: **at the driver's current limit
      → electrical → F1; inside limits but still slow → mechanical → F2**
- [ ] Hard stop at 3 rounds, non-convergence reported

---

## Verify-before-claiming

Every box above needs a number or a command output beside it, not a tick alone. The three
M1 bugs all produced *plausible* output; two of them would have passed a tick-only review.
