# Robot Design Platform — Combined Tech Stack (v3)

One loop: **intent → Robot IR → CAD → physics props → simulation → optimization → agent
revision → back to IR**, with a fixed rule at its center:

> **The agent proposes. The harness disposes.** An agent may generate any design,
> geometry, or critique — it may never declare a design valid. Only `evaluate()`
> returns a verdict. If agent reasoning and harness output disagree, the harness is
> right, and the question is why the agent was wrong.

v3 adds three things v2 waved at but did not close: **where physical numbers come
from** (§3 — every mass, inertia, and torque now has a computed or sourced origin, no
gaps), **sourcing real parts and real CAD models online** (§6 — distributor APIs and
manufacturer CAD, with provenance and license rules), and **electronics co-design via
`pcb-ai`** (§7 — the working PCB pipeline becomes the deterministic subsystem that
designs the robot's boards, and its outputs flow back into the IR as measured facts).

This supersedes all prior drafts — pull from here first.

---

## 1. The stack, layer by layer

| Layer | Tool | Role | Resolved decision |
|---|---|---|---|
| Language | **Python** | Everything downstream speaks Python natively | unchanged; `pcb-ai` stays TypeScript behind a CLI boundary (§7) |
| Design intelligence | **Claude** | Proposes designs and mechanisms, never verdicts | Phase 1: one agent. Phase 2: split into a graph (§8) |
| Robot representation | **Pydantic v2** + custom **Robot IR** | The single source of truth every other layer reads/writes | topology is data, not code (§2); gains an `electronics` subsystem (§7) |
| CAD | **build123d → OpenCascade** | Parametric B-rep solids, not triangle soup | unchanged; also the source of computed mass properties (§3) |
| Fast robotics math | **Pinocchio** | Kinematics, Jacobians, inverse dynamics — the inner loop | **tier 1 only**, ~10× Drake at this tier |
| Verification-grade dynamics | **Drake** | Rigorous multibody, contact, static-equilibrium proofs | **tier 3 only** — never the inner loop |
| Fast contact simulation | **MuJoCo** | Thousands of rollouts; the coarse filter | tier 2 |
| Electronics co-design | **`pcb-ai`** (the working LangGraph PCB loop) | Designs every board the robot needs; returns Gerbers, BOM, board mass/CoM, dissipation | **new** — subprocess over a file contract, never imported (§7) |
| Part & CAD sourcing | **Nexar/Octopart + Digi-Key/Mouser/LCSC APIs**, manufacturer STEP, McMaster-Carr, SnapEDA/UltraLibrarian | Real parts with real numbers and real 3D models, fetched and cached locally | **new** — §6; downloaded CAD is visuals + cross-check only, never mating features |
| Aerodynamics (only if in scope) | OpenRocket + ThrustCurve.org + AeroSandbox | Don't hand-roll aero | out of scope for v1–v3 unless the product pivots |
| Numeric optimization | SciPy → pymoo (NSGA-II) → Ax/BoTorch → JAX-surrogate | Progression by evaluation cost | JAX cannot autodiff through CAD (§5) |
| Units | **pint** at every IR boundary | A bare float never crosses an interface | **new** — the Python analogue of tscircuit's unit strings, same failure class |
| Backend | **FastAPI** | Every long op returns a job id | unchanged |
| Job queue | **Dramatiq** on **Redis** | Retries, dead-letter, heartbeats | Phase 2 |
| Database | **PostgreSQL** | Designs, revisions, evaluations, catalogue, **sourcing cache index** | Phase 2 |
| Object storage | **MinIO** | STEP/STL/GLB/URDF/MJCF/USD artifacts + fetched vendor CAD + `pcb-ai` run dirs | Phase 2 |
| Orchestration | **LangGraph** + postgres checkpointing | Durable, resumable multi-agent graph | Phase 2; the same framework `pcb-ai` already uses, deliberately |
| Frontend | **React + TypeScript** | Application shell | unchanged |
| 3D viewer | **Viser** + `<model-viewer>` | `pcb-ai`'s board GLB drops straight into both | unchanged |
| Robot interop formats | URDF, MJCF, USD exported from IR | Exporters, not sources of truth | unchanged |
| Deployment | Docker Compose, one machine | Local-first is a hard constraint; distributor API calls are the sole network exception, cached aggressively (§6) | Phase 1 skips even Compose |
| GPU | None required | CAD and MuJoCo are CPU-bound | Isaac Sim optional, skippable |
| Real-hardware bridge | ROS 2 | Only once sim drives a real controller | not before Phase 3 |

---

## 2. Robot IR — non-negotiable shape

Topology is data, not code: a quadruped and a rover differ by IR document, not by
Python. Rules from prototype failures, not to be relaxed:

- **`CatalogueParam`, never a free scalar, for any real part.** An optimizer will
  exploit any variable not tied to a purchasable component — a continuous shoulder-gear
  ratio converged on 7.5:1, which nobody manufactures. Discrete catalogue keys only.
- **`Quantity` requires `Provenance`.** No bare floats anywhere near a physical
  constant. `CONFIRMED | INFERRED | ASSUMED | MEASURED` — see §5.
- **`Quantity` requires a unit** (pint). `20` is not a torque; `20 kg·cm @ 12 V` is.

```python
class CatalogueParam(BaseModel):
    kind: Literal["catalogue"] = "catalogue"
    value: str           # catalogue key, e.g. "stepper_motors/17HS4401"
    catalogue: str       # e.g. "stepper_motors"

class Provenance(BaseModel):
    status: Literal["CONFIRMED", "INFERRED", "ASSUMED", "MEASURED"]
    source: str          # resolvable: datasheet URL/hash, API response id, run dir
    note: str = ""
```

**The catalogue holds real parts with datasheet numbers, not seed values** — and as of
v3 it is *populated by the sourcing layer* (§6), not by hand. The `CatalogueParam` rule
stops an optimizer inventing a 7.5:1 gearbox, but the guarantee is only as good as the
table behind it: a made-up torque on a real part number fails identically, and later.

Stock `stepper_motors`, `gearboxes`, `servos`, `dc_gearmotors`, `motor_drivers`,
`mcus`, `encoders`, `batteries`, `fasteners`, `materials` — and note that
`motor_drivers`, `mcus`, `encoders`, and `batteries` are **the same parts `pcb-ai`'s
parts agent selects**. One catalogue, keyed by manufacturer part number with distributor
IDs (LCSC especially — it decides JLC assembly eligibility), serves both pipelines. Two
catalogues would drift; drift means the robot budgets a motor the board can't drive.
Full entry schema and per-class seeding plan: **`text-to-cad-plan.md` §8.5**.

Two specifics that bite, unchanged:

- **Every torque carries its condition.** A stepper's holding torque is not its torque
  at speed; "20 kg·cm" without a voltage is not a spec.
- **Motor and gearbox are separate entries composed at resolve time**, with the
  reducer's efficiency applied (typically 0.7–0.9 planetary). One baked key hides the
  motor and blinds the optimizer to every other ratio.

Geometry generators are a **registry** (`tube`, `plate`, `bracket`, `pcb_bay`, ...),
never a hierarchy. Revisions are **immutable and append-only**.

**New in v3 — the `electronics` subsystem.** The IR gains first-class electronics
nodes: rails (voltage, budgeted current), boards (envelope, mounting pattern, connector
faces, keepouts), and harnesses (which connector on which board reaches which actuator).
These are *requirements* the robot side owns and *facts* the PCB side fills in — see §7
for the contract. A robot with a motor and no driver rail is an ERC failure at the
robot level, before `pcb-ai` ever runs.

---

## 3. Physics stack — resolved, and now closed at the inputs

The v2 tiers stand. What v2 left open is where the numbers feeding them come from.
Every physical quantity in a simulation now has exactly one of four origins, and a
tier is only as trustworthy as its worst input:

| Quantity | Origin | Provenance ceiling |
|---|---|---|
| Link mass, CoM, inertia tensor | **computed** from the B-rep + material density (OpenCascade `GProp`), never entered by hand | CONFIRMED (density) → the derived tensor is INFERRED |
| Motor/gearbox torque, speed, current | catalogue, sourced per §6 | CONFIRMED (datasheet) |
| Actuator torque *at operating point* | torque–speed curve at the actual rail voltage, thermally derated | INFERRED |
| Battery capacity, sag, C-rating | catalogue + discharge model | CONFIRMED / INFERRED |
| Board mass, CoM, dissipation | **measured by `pcb-ai`** from the routed board (§7) | MEASURED-class deterministic artifact |
| Friction, contact params | ASSUMED until Phase 3 HIL | ASSUMED — and reported as such on every tip-over verdict |

```
tier 0  analytic     mount fit, reach, static margin, rail budget   <1 ms   every candidate
tier 1  Pinocchio    torque budgets, CoM, workspace                 ~1 ms   every candidate
tier 2  MuJoCo       contact, settling, tip-over                    ~1 s    survivors of 0/1
tier 3  Drake        equilibrium proofs, contact-rich verification  ~30 s   designs about to ship
```

Additions, all borrowed from what made the PCB pipeline trustworthy:

- **Cross-checked mass properties.** Total mass and CoM are computed independently by
  the CAD layer (B-rep integration) and by the assembled MuJoCo model (sum over
  bodies). Disagreement beyond tolerance is a **pipeline bug filed automatically**, not
  a design finding — the same two-implementations rule `pcb-ai` applies to DRC.
- **Electro-mechanical actuator model at tier 0/1.** Torque available is
  `curve(voltage_at_motor) × ratio × η`, where `voltage_at_motor` accounts for battery
  sag and harness drop — numbers that come *from* the electronics subsystem, so an
  undersized rail fails the torque budget here, cheaply, instead of surviving to a
  MuJoCo rollout that assumed nominal voltage.
- **Energy tier at tier 0.** Battery discharge vs. the mission's duty cycle: runtime,
  peak-draw vs. C-rating, and the total load the PCB spec (§7) will inherit as its
  rail budgets. Trivial arithmetic, catches the "great robot, 4-minute battery" class.
- **A tier that didn't run is reported skipped, never silently a pass** — unchanged,
  and now every report also states the worst provenance among its inputs, so a
  tip-over PASS built on ASSUMED friction says so in the verdict line.

---

## 4. Optimization — by evaluation cost, not sophistication

| Phase | Method | Use when |
|---|---|---|
| v1 | Coordinate descent + catalogue enumeration | Deterministic, debuggable, no tuning |
| v1.5 | `pymoo` (NSGA-II) / `scipy` DE | Multi-objective, mixed discrete/continuous |
| v2 | Bayesian optimization (Ax/BoTorch) | Evaluations cost seconds — sample efficiency matters |
| v3 | Surrogate network + JAX | Only once thousands of stored evaluations exist |

**JAX cannot be bolted onto the CAD pipeline** — OpenCascade booleans aren't
differentiable. Three legitimate uses only: trajectory optimization on a fixed design
(MJX), a surrogate always re-verified by the real harness, and closed-form analytic
criteria. Optimize the physical quantity (drag in Newtons), never a normalized
coefficient — a Cd-only objective once inflated body diameter and *raised* drag.

One addition: **`pcb-ai` runs are never inside the optimizer's inner loop.** A board
respin costs minutes; the outer design search treats the electronics as fixed once the
spec is stable, and re-runs `pcb-ai` only when the electronics *spec hash* changes
(rails, envelope, connector set, budgets) — the same stage-cache-by-input-hash rule the
PCB plan uses for its own ladder.

---

## 5. Provenance ladder

`CONFIRMED` (manufacturer drawing/datasheet) → `INFERRED` (derived or secondary) →
`ASSUMED` (chosen by us) → `MEASURED` (instrumented hardware, Phase 3 only — the one
status that outranks CONFIRMED). CI fails if a `CONFIRMED` entry lacks a resolvable
source.

**Vendor CAD is for visuals only** — never cut a mating feature from downloaded CAD;
bolt patterns and shaft diameters come from datasheet constants. §6 operationalizes
this: fetched models are tagged at ingest and the CAD layer *refuses* boolean
operations against any solid tagged `visual_only`. The rule stops being discipline and
becomes a type error.

Deterministic outputs of `pcb-ai` (board mass from BOM + stackup, dissipation from its
solvers, DRC/DFM status) enter the IR as **MEASURED-class**: they are computed by an
independent gated pipeline from the routed artifact, not asserted by any agent.

---

## 6. Part & model sourcing — real parts, real models, fetched not typed

New in v3, and the answer to "the catalogue is only as good as the table behind it":
the table is built by a **sourcing layer** that pulls from the real world, caches on
disk, and stamps provenance on everything it touches.

### 6.1 Where numbers come from (parametric data)

| Source | Gives | Serves |
|---|---|---|
| **Nexar (Octopart) API** | cross-distributor search, specs, datasheet links, lifecycle | catalogue seeding, alternates |
| **Digi-Key / Mouser APIs** | parametric attributes, price breaks, live stock, CAD links | catalogue + BOM pricing |
| **LCSC / JLC parts API** | stock + assembly eligibility (basic/extended) | shared with `pcb-ai`'s BOM — a part the fab can't place is flagged at selection, not at order time |
| Manufacturer datasheets (PDF) | torque curves, derating, dimensioned drawings | the numbers that gate |

Datasheet ingestion is agent work with a deterministic leash: a `librarian` agent
extracts values from the PDF into a catalogue entry, every value cites page/figure, and
the entry lands as **INFERRED**. Promotion to CONFIRMED requires a human check of the
cited figure — a checkbox per value, minutes of work, and the only place a human number
review exists in the loop. Extracted curves (torque–speed, discharge) are stored as
point tables with the source figure hashed alongside.

### 6.2 Where models come from (3D CAD)

| Source | Format | Trust |
|---|---|---|
| Manufacturer downloads (STEP from the part page) | STEP | best available; still `visual_only` for mating |
| **McMaster-Carr** | STEP, per-SKU | fasteners/hardware; dimensions independently in the datasheet table |
| **SnapEDA / UltraLibrarian** | STEP + footprint + symbol | one fetch serves **both** pipelines — the 3D body for the robot bay, the footprint for `pcb-ai` |
| GrabCAD / community | STEP/mesh | last resort, license reviewed, always `visual_only`, never dimensionally trusted |

Ingest pipeline, fully deterministic: fetch → hash → license recorded → units and axes
normalized (mm, Z-up, origin at the datasheet datum) → watertightness/scale sanity
check → **mass cross-check**: the model's volume × catalogue density must land within
tolerance of the datasheet mass. A motor model 40% off on mass is mis-scaled or hollow,
and it's caught at ingest instead of skewing every CoM downstream. Failures quarantine
the model; the part keeps a parametric placeholder (cylinder/box from datasheet
dimensions) so the pipeline never blocks on a pretty model.

Everything fetched is cached in the artifact store keyed by content hash. The network
is touched at catalogue-build time only; evaluation and optimization remain fully
offline. API keys are config, absence degrades to cache-only with a loud warning —
local-first holds.

### 6.3 What agents may and may not do here

An agent may *propose* a part or *request* a search ("NEMA17 class, ≥0.4 N·m at speed,
≤350 g"). The fetch, parse, normalize, and cross-check are tools. An agent never types
a number into the catalogue — the librarian extracts with citations, the harness
checks, a human confirms. Same rule as everywhere: proposes, never disposes.

---

## 7. PCB co-design — `pcb-ai` as a deterministic subsystem

The robot needs boards: a motor-driver carrier, a power distribution board, a sensor
breakout. v2 had nowhere for them to come from. v3 plugs in the **working `pcb-ai`
pipeline** (see its README/PLAN) as a black-box stage — the robot side treats it
exactly the way it treats MuJoCo: a deterministic engine invoked with a spec, returning
measured artifacts.

### 7.1 The boundary

`pcb-ai` is TypeScript; this platform is Python. They are **never linked** — the
integration is a subprocess and files on disk, which both codebases are already built
around (`pcb-ai` keys everything to a run directory; the engine here is a pure library
with zero I/O):

```
robot side                                     pcb-ai side
──────────                                     ───────────
IR electronics subsystem
  └─ emit board-spec.md + envelope.json  ──►   npm run design -- --spec board-spec.md
       rails, budgets, connectors,              (its own ladder: lint → compile → ERC →
       envelope, mounting, keepouts,             place → route → DRC×2 → physics →
       placement_rules, fab profile              SPICE → DFM → artifacts)
                                         ◄──   runs/<dir>/
  ingest as MEASURED-class facts:                summary.json      pass/fail, per-gate
    board mass + CoM (BOM + stackup)             fabrication/      gerbers, drill, BOM,
    dissipation map (its solvers)                                  pick-and-place
    connector positions (Circuit JSON)           board GLB         → robot CAD + viewer
    DRC/DFM/SPICE gate status                    circuit.json      geometry + connectors
```

### 7.2 What flows down (robot → board spec)

The IR's electronics subsystem compiles to exactly what `pcb-ai`'s intake wants —
functions, rails, envelope, connectors, budgets — plus the mechanical facts only the
robot knows:

- **Envelope and mounting**: board outline, hole pattern, keepouts under mechanism
  clearances — from the CAD `pcb_bay` generator, so the board *cannot* be designed
  bigger than its bay.
- **Rail budgets**: from the tier-0 energy analysis — worst-case per-motor current at
  stall, MCU/sensor loads, with the margin policy stated.
- **Connector placement as `placement_rules`**: "motor connectors on the edge facing
  the harness channel" becomes `at_edge`/`opposite_edges` rules `pcb-ai` enforces
  deterministically at its L3 — the robot's ergonomic intent becomes the board's hard
  gate.
- **Fab profile**: the same `.kicad_pro`-derived DFM profile, chosen once for the
  project.

### 7.3 What flows back (board → IR)

- **Mass and CoM** of the populated board (BOM masses + stackup density × area) — into
  the robot's mass model as MEASURED-class, replacing the ASSUMED "50 g per board"
  placeholder that Phase 1 starts with.
- **Dissipation** per board from its thermal solve — a heat source the enclosure
  design sees; conversely the robot supplies the boundary condition (enclosed? airflow?)
  the PCB thermal model needs, closing the loop `pcb-ai`'s "what is not modelled"
  section explicitly leaves open.
- **Connector positions** from Circuit JSON — harness lengths stop being guesses;
  harness resistance feeds the voltage-drop term in the actuator model (§3).
- **Gate status**: a robot revision cannot pass while its board is failing DRC/DFM/
  SPICE. The board's hard failures are hard failures of the robot, by construction —
  the chief-cannot-waive rule, inherited.

### 7.4 What is shared

- **The parts catalogue** (§2): motors, drivers, MCUs, encoders, batteries — one table,
  distributor-keyed, so the parts agent on either side selects from the same reality.
- **The provenance discipline**: their unit-strings ≙ our pint; their operating point
  as a checked artifact ≙ our Provenance ladder; their "measurement vs judgement" ≙
  our "agent proposes, harness disposes". Same philosophy, two codebases — kept aligned
  by the contract being *files with schemas*, versioned, with a golden round-trip test
  (a reference robot spec → board spec → `pcb-ai --model stub` → ingest) in CI on both
  repos.
- **LangGraph**: not shared state, but shared idiom — checkpointed, resumable,
  budget-in-state on both sides, so operating one teaches you the other.

### 7.5 Sequencing the integration

1. **I1 — one-way, manual**: robot emits `board-spec.md`; a human runs `pcb-ai`; robot
   ingests the run dir. Proves the contract with zero orchestration code.
2. **I2 — automated invoke**: subprocess + spec-hash cache (§4); board facts land in
   the IR automatically; golden round-trip in CI.
3. **I3 — closed thermal/electrical loop**: enclosure boundary conditions down,
   dissipation and harness drops up, iterate to fixpoint (in practice: twice).

---

## 8. Agents — one first, split only when earned

Start with **one designer agent** plus deterministic `evaluate`/`critique` nodes. Split
only when a single prompt genuinely can't hold both roles, or when per-agent
prediction-accuracy data says a split helps.

**Phase 2 graph** (LangGraph, Postgres-checkpointed): `intent`/`vision` synthesize an
IR draft → deterministic `SUPERVISOR` (never an LLM) routes on Report content and
budget → `designer` / `critic` / `criteria_author` propose; deterministic
`evaluate`/`probe`/`coverage_verify` score → `sim_analyst` names failure mechanisms →
on all-pass, `sourcing_agent` builds the BOM. v3 adds two roles:

- **`librarian`** (§6.1) — datasheet extraction with citations, output is a checked
  artifact, never trusted prose. Mid-tier multimodal model; it reads figures.
- **`electronics_liaison`** — owns the board-spec emission and the ingest of `pcb-ai`
  verdicts into the work order. It translates; it never overrides a board gate.

Every agent emits a structured, Pydantic-validated schema. A `Proposal` records its
**predicted** effect before evaluation, scored after — per-agent prediction accuracy is
the signal for whether an agent is reasoning or guessing, and for whether a local model
suffices for a role. BLIND findings (§9) route to `criteria_author` **before** further
search — optimizing a space the harness can't measure is wasted compute by construction.

---

## 9. Criteria & coverage — the actual moat

A criterion transfers across topologies even when the CAD doesn't — `mount_fits`
written for a rover caught the same defect class on a quadruped, unchanged.

- Every criterion exposes a **magnitude**, not just pass/fail — booleans are invisible
  to coverage analysis.
- Prefer ratios and forces over angles and coefficients — an angle-based stability
  criterion registered 1.4% sensitivity where the tan-ratio form registered 5.7%;
  `atan` saturates and hides signal.
- **Coverage analysis**: perturb every design variable ±10%, re-evaluate, measure each
  criterion's response. Real coverage measures 3–16%; an unmeasured variable 0.0–0.1% —
  two orders of magnitude apart. `BLIND` = no criterion responds (harness bug, needs a
  new criterion, not more search). `FRAGILE` = a pass flips to fail (no margin).
- **New:** electronics-boundary criteria are first-class — `rail_margin`
  (budgeted vs. worst-case draw, as a ratio), `board_fits_bay` (clearance in mm),
  `harness_drop` (volts at stall), `board_thermal_budget` (dissipation vs. what the
  enclosure sheds). Coverage perturbation reaches through the boundary: perturbing
  motor choice must move `rail_margin`, or the electronics subsystem is BLIND and the
  integration is decorative.

---

## 10. Deliberately excluded (and why)

| Excluded | Reason |
|---|---|
| Gazebo / SDF | Redundant — MuJoCo covers fast contact, Drake covers verification |
| Cloud CAD (Onshape/Fusion/Zoo) | Violates local-first; build123d gives scriptable local B-rep |
| Full LangChain | Only `langchain-core` primitives are needed |
| Temporal | Heavy always-on cluster for a single-box product; Dramatiq suffices |
| CFD (OpenFOAM/SU2) | Only if the product pivots aero-heavy; AeroSandbox until then |
| Isaac Sim | Needs RTX on Linux/Windows; skip entirely, the pipeline already emits USD |
| ROS 2 ahead of need | Only once sim drives real hardware |
| **Rewriting `pcb-ai` in Python** | It works, it's gated, it's regression-tested. A port buys unification of language and pays with re-verifying ten gate stages. Subprocess + file contract costs one schema. |
| **Importing `pcb-ai` as a library / merging the graphs** | One LangGraph reaching into another couples checkpoint stores and failure domains. The board run is atomic from the robot's view: spec in, verdict + artifacts out. |
| **Scraping CAD portals without APIs** | Brittle and license-hostile; distributor APIs + manufacturer downloads + SnapEDA cover the need |

---

## 11. Phased roadmap

**Phase 1 — core loop, single agent, local-only** *(the demoable slice)*
IR + geometry registry → **computed mass properties (tier 0, §3)** → tier 0/1 criteria +
`evaluate` CLI → MuJoCo tier → coverage/BLIND analysis → single-agent loop → minimal
frontend. Catalogue seeded by the **sourcing layer for two part classes** (motors,
batteries) end-to-end — fetch, ingest, cross-check, human-confirm — rather than v2's
hand-typed stub, so the torque axis has real coverage from the start.
**Exit test:** a text description converges, unattended, to a design passing every
applicable criterion with margin — on SQLite, no Docker, network touched only at
catalogue build.

**Phase 2 — services, full agent graph, PCB integration I1–I2**
Promote to Postgres/Redis/MinIO. Split the agent graph where Phase 1's
prediction-accuracy data justifies it. Sourcing layer covers all catalogue classes;
librarian agent online. **`pcb-ai` integration to I2**: automated invoke, spec-hash
cache, board facts in the IR, golden round-trip in CI. Coverage matrix frontend — the
one screen showing what the system isn't checking — now including the electronics-
boundary criteria.

**Phase 3 — verification + real hardware** *(gated, may never trigger)*
Drake tier for anything presented as final. **PCB integration I3** (closed
thermal/electrical loop). HIL only after closed-loop actuators (Dynamixel-class /
ODrive/Moteus — open-loop steppers have no telemetry and are a dead end). Every HIL run:
human-approved `interrupt()` before actuation, firmware current limits set before first
motion, physical e-stop. Non-negotiable. MEASURED provenance becomes reachable, and the
first measurements to take are the ones the ladder currently carries as ASSUMED:
friction, contact, real harness drops.

---

## 12. Non-negotiables

1. Only propose parts that exist — catalogue keys, never free scalars for physical components.
2. Never freehand a computed number — inertia, CoM, torque budgets are computed, not guessed.
3. Label provenance — CONFIRMED ≠ INFERRED ≠ ASSUMED ≠ MEASURED — and every verdict states the worst provenance among its inputs.
4. Never cut mating features from vendor/community CAD — enforced by the `visual_only` tag, not by discipline.
5. Report what was skipped — a tier that didn't run is not a pass.
6. State predictions before evaluating; report plainly when they're wrong.
7. The engine has zero I/O — if it imports the ORM, the architecture is broken.
8. Revisions are immutable.
9. A number enters the catalogue only through the sourcing pipeline with a citation — no agent, and no human in a hurry, types one in directly.
10. A board gate failure is a robot failure. No robot-side agent — chief included — can waive a `pcb-ai` hard failure.

---

## 13. Implementation status (2026-08-15)

What of the above exists in this repo, stated plainly, because a spec that does
not say which parts are built is a spec that gets cited as though all of it is.

| § | Built | Where |
|---|---|---|
| §1 `pint` at every boundary | yes | `engine/units.py`; `Quantity` validates its unit on construction |
| §2 electronics subsystem | yes | `Rail`, `BoardSpec`, `Harness`, `Electronics` in `engine/ir.py`; optional, and its absence is *reported*, not passed |
| §2 catalogue classes | 8 of 10 | `batteries`, `mcus`, `encoders` added; `dc_gearmotors` and `fasteners` not stocked |
| §3 computed mass properties | already existed | `engine/mass_properties.py` |
| §3 CAD↔MuJoCo cross-check | yes | `engine/crosscheck.py`; agrees to 1e-10 on real designs, and a disagreement is a `PipelineBug`, not a criterion |
| §3 actuator at real voltage | yes | `engine/electrical.py`; validated to 3% against an independently sourced 7.4 V catalogue entry |
| §3 energy tier | yes | `energy_runtime`, `peak_draw_within_c_rating` at tier 0 |
| §3 worst provenance on every verdict | yes | `CriterionResult.provenance`, `EvaluationReport.worst_provenance` |
| §5 `visual_only` as a type error | yes | `sourcing/models.require_matable()`, wired into `assets.load_step(mating=True)` |
| §6.1 distributor providers | yes, unproven | Nexar/Digi-Key/Mouser/LCSC over `urllib`, offline by default. **No live call has been made** — no keys on this box |
| §6.1 librarian | contract only | `sourcing/librarian.py` defines what an extraction must look like and who may confirm it. The extracting agent is not written |
| §6.2 model ingest | yes | fetch→hash→licence→normalise→mass cross-check→quarantine, with a parametric fallback |
| §7.2 board spec down | yes | `engine/export/board_spec.py`, envelope validated against the contract file |
| §7.3 facts up | yes | `engine/ingest/pcb_run.py`; board mass added to `pcb-ai`'s `board_report` to make the MEASURED claim true |
| §7.5 sequencing | at **I1** | emit → human runs `pcb-ai` → ingest. I2's subprocess and spec-hash cache are not wired; the hash exists |
| §8 `librarian` / `electronics_liaison` agents | no | the deterministic halves both roles sit on top of are built |
| §9 electronics criteria | yes, plus one | the four named, plus `electronics_erc`, `board_gate_passed`, and `actuator_voltage_in_range` |
| §9 coverage through the boundary | yes | `analyze_coverage` perturbs electronics variables; `analyze_catalogue_coverage` swaps parts |

**The honest gaps.**

- **The catalogue predates its own pipeline.** Every number in it was
  transcribed, which is the practice §12 #9 exists to end. The debt is
  enumerable rather than asserted away: `python -m engine.sourcing debt` reports
  85 unconfirmed values, 26 of them ASSUMED. Three battery internal resistances
  head the list — nothing else in the model is as sensitive.
- **`joint_torque_budget` saturates on an unloaded joint.** It reports
  `(available − required) / available`, which goes to 1 as the required torque
  goes to zero, so a rover wheel at rest is blind to which motor is fitted. That
  is physically correct and it means the static criterion is the wrong
  instrument for a wheel. `actuator_headroom` against an accelerating load is the
  right one and is not written.
- **`board_thermal_budget` is ASSUMED at both ends.** A convection coefficient of
  6 W/m²K and a 25 K allowed rise are ours, not anyone's measurement. It reports
  ASSUMED however MEASURED the dissipation is, which is the correct answer and
  not a satisfying one; step I3 replaces it with `pcb-ai`'s solve against a
  boundary condition this side supplies.
- **Component masses in `board_report` come from a footprint table.** Same
  honesty structure as the height table beside it, and less load-bearing — a
  0.5 g error on a passive is invisible next to a 12 g substrate, whereas a 4 mm
  height error on an electrolytic is an enclosure that does not close.
