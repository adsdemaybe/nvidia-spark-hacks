# Electromechanical co-simulation — proving the board moves the robot

**Status:** plan, ready for coding. Sibling to `text-to-pcb-plan.md` (F1) and
`text-to-cad-plan.md` (F2); extends `master-plan.md` §6 from a *geometric* negotiation
into a *physical* one. Working directory: `pcb-ai/` for the electrical half,
`cad-generation/` for the mechanical half, and a shared broker between them.

---

## 1. The question nothing currently answers

F1 proves a board is well-formed, manufacturable, and electrically sane at DC. F2 proves
an enclosure fits it. §6's negotiation proves the two agree about *geometry*.

None of that proves the thing the robot actually needs:

> **When the microcontroller drives a pin, does enough current reach the motor, through
> this trace, past this gate driver, to produce the torque that moves this joint — and
> does the joint move fast enough that back-EMF doesn't collapse the current?**

That is one question spanning three domains, and every existing gate answers a slice of
it in isolation. A board can pass L7 at DC and stall the arm, because L7 never sees the
motor's inductance, the driver's gate delay, or the fact that at 3000 rpm the back-EMF
eats most of the supply. A chassis can pass its CAD criteria and be un-driveable, because
CAD never sees that the trace to the motor is 0.3 mm and drops 800 mV under load.

**The deliverable is a loop that fails when either of those is true.**

---

## 2. Why not a field solver

The obvious move is a 3D electromagnetic solver — Maxwell, openEMS — on the routed
copper. It is the wrong tool here, for a reason that is about the loop rather than the
physics:

| | field solver | what this loop needs |
|---|---|---|
| Time per evaluation | minutes to hours | **milliseconds** |
| Evaluations per design round | 1, maybe | thousands of timesteps |
| Answers | parasitic extraction, EMC, SI | does the joint reach position |

A design loop that runs 2–3 negotiation rounds, each with a physics rollout, needs the
electrical answer thousands of times. At minutes per solve that loop takes days; at
milliseconds it takes seconds. **The board's behaviour is a lumped-element question at
this scale** — trace resistance, driver on-resistance, motor R/L, supply sag — and a
transient circuit simulator answers exactly that, fast.

Field solving stays on the table as a *post-hoc* check on a converged design, never
inside the loop. Recorded here so the choice is deliberate and revisitable, not an
oversight.

---

## 3. Architecture

```
  ┌── firmware model ──┐     ┌──── pcb-ai ────┐     ┌─── cad-generation ───┐
  │  PWM duty, dir,    │     │  transient     │     │  build123d geometry  │
  │  control period    │     │  SPICE deck    │     │  + mass properties   │
  └─────────┬──────────┘     └───────┬────────┘     └──────────┬───────────┘
            │ mcu/output             │ pcb/physics             │ URDF / MJCF
            ▼                        ▼                         ▼
  ═══════════════════ the wire — ZeroMQ pub/sub, in RAM ═══════════════════
            ▲                        ▲                         │
            │ sensor/encoder         │ motor/state (ω, load)   │ motor/input
            └────────────────────────┴─────────────────────────┘
                                                     ┌─────────▼──────────┐
                                                     │  MuJoCo, headless  │
                                                     │  joints, contacts  │
                                                     └────────────────────┘
```

Three processes, one message bus, no shared memory and no file handoff inside the loop.
The bus is the contract, so any of the three can be replaced — a different simulator, a
real MCU over serial, a bench supply — without the others noticing.

### 3.1 Why ZeroMQ rather than gRPC

Both were considered. gRPC is the better choice for a *service* boundary: typed
contracts, versioning, streaming with flow control. This is not a service boundary — it
is a **wiring harness**, and the traffic pattern is a fixed set of topics broadcast every
timestep to whoever is listening.

- **Latency budget.** At a 1 kHz control period the whole round trip must fit in under a
  millisecond. ZeroMQ `inproc`/`ipc` sockets are tens of microseconds; gRPC's HTTP/2
  framing and protobuf round trip is an order of magnitude more, per hop, and there are
  three hops.
- **Pub/sub is the actual shape.** A wire has no request/response. The MCU model does not
  *ask* the board for current; the board broadcasts what it is doing and whoever cares
  listens. Modelling that as RPC inverts the physics.
- **Late joiners and drops are correct behaviour.** A subscriber that misses a frame
  should see the next one, not block the publisher. That is ZeroMQ's default and gRPC's
  problem to solve.

gRPC is still right for the *design-time* API — `/pcb/...` and `/cad/...` already are
HTTP/JSON — and this plan does not touch those. **Design-time is REST; run-time is
ZeroMQ.**

### 3.2 Topics and payloads

Every message is JSON with a `t` (simulation seconds) and a monotonic `seq`. JSON rather
than protobuf to start: the volume is small, the debuggability is worth more than the
bytes, and swapping the encoder later touches one module.

| Topic | Publisher | Payload |
|---|---|---|
| `mcu/output` | firmware model | `{t, seq, pins: {PA0: {duty, freq_hz, dir}}, …}` |
| `pcb/physics` | pcb-ai | `{t, seq, motors: {M1: {voltage_v, current_a, torque_nm}}, rails: {V3V3: {v, sag_mv}}, warnings: []}` |
| `motor/input` | pcb-ai (alias of the above, per-motor) | `{t, seq, motor, torque_nm}` |
| `motor/state` | MuJoCo | `{t, seq, motors: {M1: {omega_rad_s, angle_rad, load_nm}}}` |
| `sensor/encoder` | MuJoCo | `{t, seq, joints: {j1: {angle_rad, vel_rad_s}}}` |
| `sim/control` | orchestrator | `{cmd: "step"|"stop"|"reset", …}` |

**`motor/state` is the one that makes this a real co-simulation rather than a pipeline.**
Torque depends on current, current depends on back-EMF, back-EMF depends on angular
velocity, and angular velocity comes from MuJoCo. Without that topic the electrical side
is solving an open-loop problem and will happily report currents that could never flow.

---

## 4. Step 1 — the behaviour engine (transient SPICE)

**What exists:** `pcb-ai/src/spice/` builds an ngspice deck from Circuit JSON and the
operating point, models ICs as behavioural current sinks, and runs `.op`. ngspice 42 is
vendored rootless (`tools/vendor-ngspice.sh`).

**What this needs that does not exist:** `.tran`. The whole L7 claim grammar today
(`dc_rail`, `node_voltage`, `current`, `current_max`) is steady-state, and §3.4 of the
PCB plan already lists `ripple`, `frequency`, `edge` and `startup` as unimplemented. Those
are the same missing capability.

### 4.1 The motor model is the crux

A DC motor is where electrical becomes mechanical, and it must appear in **both**
simulators or the coupling is fiction:

```
electrical (SPICE):        V = I·R_m + L_m·dI/dt + Ke·ω
mechanical (MuJoCo):       τ = Kt·I,   J·dω/dt = τ − τ_load − b·ω
```

- `R_m`, `L_m`, `Ke`, `Kt` come from the motor's datasheet and carry
  `CONFIRMED | ASSUMED` provenance like every other constant in this project. A motor
  with assumed constants produces an answer labelled as such.
- **ω is an input to the SPICE deck**, supplied per control period from `motor/state`.
  It enters as a voltage source in series with the winding (back-EMF).
- **I is an output**, converted to torque by `Kt` and published on `motor/input`.

This is the coupling, and it is why `motor/state` exists.

### 4.2 Timestep coupling

SPICE wants microseconds (PWM edges at 20 kHz need ~1 µs resolution). MuJoCo runs at
0.5–2 ms. They cannot share a clock, so:

1. MuJoCo publishes `motor/state` at the start of a control period.
2. pcb-ai runs `.tran` over exactly one control period, with ω held constant, and PWM
   driven from `mcu/output`.
3. pcb-ai publishes the **average** torque over that period, plus peak current for the
   thermal and DFM gates.
4. MuJoCo steps its own substeps with that torque held constant, and publishes the new ω.

This is explicit (Jacobi) coupling and **it can go unstable** when the electrical and
mechanical time constants are close — a real, known failure mode of co-simulation, not a
hypothetical. Mitigations, in order: shorten the control period; hold ω from the previous
period rather than extrapolating; if it still rings, move to a single-rate model where the
motor's electrical dynamics are solved inside MuJoCo as a first-order lag. **A rollout
that diverges is reported as divergence, never as a result.**

### 4.3 What L7 gains

Transient analysis makes four of the plan's promised claim kinds implementable, and adds
the ones this loop needs:

| Claim | Asserts |
|---|---|
| `startup(net, v, t_max)` | the rail comes up within a deadline |
| `ripple(net, mv_pp, load)` | supply sag under switching load |
| `edge(net, t_rise_max)` | gate-drive delay is inside budget |
| `pwm_current(motor, a_avg, a_peak)` | the driver delivers the current the motor needs |
| `stall_current(motor, a_max)` | a stalled motor does not exceed the driver or trace rating |

`stall_current` matters most: it is the case that destroys hardware, and it is exactly
what a DC-only analysis cannot see.

---

## 5. Step 2 — the wire

A small Python package, `cosim/`, owning the broker and the message schemas. Deliberately
tiny: a bus with opinions becomes a framework, and this one needs to stay replaceable.

- `cosim/bus.py` — publisher/subscriber wrappers over `pyzmq`, one XPUB/XSUB proxy so
  every participant connects to a fixed address rather than to each other.
- `cosim/schema.py` — Pydantic models for each topic, shared by all three participants.
  A malformed message is dropped **and counted**; a silent drop is how co-simulations
  lie.
- `cosim/clock.py` — the orchestrator. Owns simulation time, issues `sim/control`, and
  enforces that every participant has published for period *n* before advancing to
  *n+1*. Without a barrier the loop free-runs and the physics is nonsense.
- `cosim/record.py` — every message to a Parquet trace, so a rollout can be replayed and
  a failure can be inspected without re-running it.

**The barrier is the part to get right.** A participant that stalls must halt the
simulation with a named timeout, not let the others run ahead against stale data.

---

## 6. Step 3 — the physics engine

**MuJoCo**, headless. PyBullet was the alternative; MuJoCo wins here because F3 already
runs it (`realsim/packages/envgen/src/envgen/cousins/mjcf.py` writes MJCF and settles
scenes today), so the project has working knowledge and one fewer dependency to learn.

### 6.1 Getting geometry out of build123d

build123d is a geometric kernel, not a dynamics engine — it has no notion of a joint, a
motor or a timestep. The bridge:

1. `cad-generation` exports each link as a mesh (STL/GLB) plus its mass properties, which
   `engine.evaluate()` already computes.
2. A joint graph — parent, child, axis, limits, damping — comes from the Robot IR the CAD
   plan already defines. **This is the piece that must not be inferred from geometry:** a
   hinge is a design decision, not something to guess from two touching meshes.
3. `cosim/urdf.py` emits URDF and MJCF from (mesh + mass + joint graph), with convex hulls
   for collision.
4. The PCB enters the same way, through the `asset_bundle` contract of PCB plan §10.5 —
   the board is a rigid body with real mass at a real offset, not a massless decoration.

### 6.2 The loop

```python
for period in range(n_periods):
    state = bus.wait("motor/state", period)        # ω, load from MuJoCo
    cmd   = bus.wait("mcu/output",  period)        # duty, direction from firmware
    elec  = spice.transient(cmd, state, dt=period_s)
    bus.publish("pcb/physics", elec)               # V, I, τ, warnings
    mj.apply_torque(elec.torque_nm); mj.step(substeps)
    bus.publish("motor/state",   mj.motor_state())
    bus.publish("sensor/encoder", mj.encoders())
```

---

## 7. The gate — what makes this a gate and not a demo

Consistent with every other stage in this project: **a task with a machine-checkable
success predicate, measured, with failure reported rather than narrated.**

| Gate | Passes when |
|---|---|
| **Coupling stable** | no divergence; ω and I bounded over the rollout |
| **Task success** | the joint reaches its commanded pose within tolerance and deadline |
| **Electrical survival** | peak current within driver and trace rating for the whole rollout, checked against the L6 thermal and IPC-2221 budgets |
| **Thermal** | dissipation over the duty cycle keeps the driver under its limit — the *duty-cycle* number, not the DC one |
| **Provenance** | every motor constant labelled; a rollout resting on `ASSUMED` constants is reported as such |

**What this does not prove**, stated so nobody over-claims it: no EMC, no signal
integrity, no PCB parasitics beyond trace R (and L where it matters), no contact-rich
manipulation fidelity, no thermal transient inside the silicon. It proves the
*electromechanical chain closes* — which is the thing currently unproven — and nothing
more.

---

## 8. The three-way loop

§6's negotiation is PCB ↔ CAD over geometry. This extends it to a third participant, and
the stop condition stays the same: **hard stop at 3 rounds, non-convergence reported.**

```
round n:
  1. pcb-ai designs / re-places the board          → board_report
  2. cad-generation designs the enclosure + links  → enclosure_report, URDF
  3. co-simulation runs the task                   → rollout verdict
  4. failures route to whoever can fix them:
       "current sags under load"    -> PCB  (wider trace, better driver, more bulk)
       "joint too slow / torque low"-> CAD  (gear ratio, link mass, joint damping)
       "board does not fit"         -> §6's existing envelope negotiation
  5. converged when the rollout passes and neither side reports a violation
```

**Routing a failure to the right side is the interesting part.** A slow joint can be an
electrical problem or a mechanical one, and the loop must not thrash between them. The
discriminator is measurable: if peak current is at the driver's limit, it is electrical;
if current is well inside limits and the joint is still slow, it is mechanical. That test
is deterministic, and it belongs in code rather than in an agent's judgement.

---

## 9. Sequencing

| Step | What | Depends on |
|---|---|---|
| **M1** ✅ | `.tran` + the motor model, standalone — **done and validated**, see below | existing `src/spice/` |
| **M2** | `cosim/` — bus, schema, clock barrier, recorder; a loopback test with two fake participants | pyzmq |
| **M3** | MJCF/URDF emission from Robot IR + meshes; one joint driven by a constant torque in MuJoCo | cad-generation, mujoco |
| **M4** | Close the loop: SPICE ↔ bus ↔ MuJoCo on one joint, with back-EMF | M1–M3 |
| **M5** | The gate: task predicate, electrical survival, thermal duty cycle, provenance | M4 |
| **M6** | Three-way negotiation with failure routing (§8) | M5, §6 loop |

M1 and M2 are independent and can go in parallel. **M4 is where the risk is** — that is
where coupling stability shows up.

### 9.1 M1, done (2026-08-15)

`pcb-ai/src/spice/motor.ts` + `transient.ts`, driven by `tools/motor-sim.ts`. Validated
by behaviour rather than by a single number, because one operating point cannot show
whether a model is a motor:

| duty (stalled) | I avg | | ω rad/s (full duty) | I avg | output torque |
|---|---|---|---|---|---|
| 10% | 0.052 A | | 0 | 1.510 A | 831 mN·m |
| 25% | 0.325 A | | 336 | 1.133 A | 623 mN·m |
| 50% | 0.758 A | | 673 | 0.755 A | 415 mN·m |
| 75% | 1.156 A | | 1009 | 0.378 A | 208 mN·m |
| 100% | 1.510 A | | 1278 | 0.076 A | 42 mN·m |

Current tracks duty; current falls linearly with speed as back-EMF rises — the textbook
torque-speed curve, and the proof that §4.1's coupling term is wired the right way round.
100% duty gives exactly 7.4 / (0.12 + 0.72 + 0.05 + 4.0) = 1.513 A, which is the
arithmetic done independently.

The stall sweep is the one that justifies the gate: the 775-class motor draws **4.65 A**
through a DRV8833 rated 1.5 A per channel. Note it is *not* its intrinsic 10.6 A stall —
the bridge, copper and supply impedance are in series with the winding, so the delivered
figure is lower. Both numbers are true and answer different questions; the catalogue now
says which is which.

**Three bugs found getting here, all worth recording:**

1. **The parser dropped every measurement.** `print` puts the value at the end of the
   line; `.meas` follows it with `from= … to= …`. The regex anchored to end-of-line, so
   every transient silently returned nothing — which reads as "the simulation found
   nothing" rather than "the parser did not look".
2. **The measurement window was shorter than the physics.** Sizing the span at 8 PWM
   cycles seemed obviously right: at 20 kHz that is 400 µs, while this motor's L/R is
   375 µs, so the winding current was still on its first ramp when the window closed. A
   1.5 A stall reported as 0.001 A. The span must cover the *slower* of the two time
   constants.
3. **No freewheel path — the model was unphysical, not merely inaccurate.** When the
   bridge opens, the winding insists on maintaining its current; with nowhere to go the
   solver drove the node hugely negative and the average collapsed. The giveaway was that
   100% duty (which never switches) was exactly right while every switching case was
   wrong. A real H-bridge always has this path: the opposite FET's body diode.

Bug 3 is the one to remember. Bugs 1 and 2 produce obviously-wrong numbers; bug 3
produced *plausible* small numbers, and a duty sweep is what exposed it.

---

## 10. Risks

| Risk | Sev | Mitigation |
|---|---|---|
| Explicit coupling diverges | **high** | §4.2's ladder: shorter period → held ω → single-rate fallback. Divergence is reported, never smoothed |
| Motor constants are guesses | **high** | provenance labels; a rollout on `ASSUMED` constants is evidence of a shape, not a number |
| MuJoCo on aarch64 | med | wheels exist for linux-aarch64; F3 already runs it. Verify at M3 |
| SPICE too slow at 1 kHz × thousands of periods | med | measure at M1. Fallbacks: longer control period, or a fitted behavioural motor model replacing the deck inside the loop |
| The bus becomes a framework | low | four files, JSON payloads, no plugins |
| Loop thrash between PCB and CAD fixes | med | §8's deterministic discriminator, and the 3-round stop |

---

## 11. What this reuses

Nothing here starts from zero, and the parts already proven are the ones worth keeping:

- **ngspice, vendored and working** — `pcb-ai/tools/vendor-ngspice.sh`, verified on aarch64
- **The deck builder** — Circuit JSON → SPICE with behavioural IC stubs and labelled
  coverage (`src/spice/netlist.ts`)
- **The claim grammar and its two-directional gate** — every claim asserted, every rail
  covered (`src/spice/index.ts`)
- **The PCB service and `replace_within`** — `/pcb/...`, the negotiation's other half
- **The CAD service** — `/cad/design_enclosure`, running on aarch64 with build123d
- **MJCF prior art** — `realsim/packages/envgen/.../mjcf.py`
- **Mass properties and the `asset_bundle` contract** — PCB plan §10.5

The new code is the transient motor model, the bus, the URDF/MJCF emitter from Robot IR,
and the gate.
