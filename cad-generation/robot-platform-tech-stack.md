# Robot Design Platform — Combined Tech Stack (v2)

One loop: **intent → Robot IR → CAD → physics props → simulation → optimization → agent
revision → back to IR**, with a fixed rule at its center:

> **The agent proposes. The harness disposes.** An agent may generate any design,
> geometry, or critique — it may never declare a design valid. Only `evaluate()`
> returns a verdict. If agent reasoning and harness output disagree, the harness is
> right, and the question is why the agent was wrong.

Every stack choice below serves that rule. This supersedes both prior drafts — pull
from here first; the earlier documents are background, not source of truth.

---

## 1. The stack, layer by layer

| Layer | Tool | Role | Resolved decision |
|---|---|---|---|
| Language | **Python** | Everything downstream speaks Python natively; avoids stitching disconnected ecosystems together | unchanged |
| Design intelligence | **Claude** | Proposes designs and mechanisms, never verdicts | Phase 1: one agent. Phase 2: split into a graph (§7) |
| Robot representation | **Pydantic v2** + custom **Robot IR** | The single source of truth every other layer reads/writes | topology is data, not code (§2) |
| CAD | **build123d → OpenCascade** | Parametric B-rep solids, not triangle soup | unchanged |
| Fast robotics math | **Pinocchio** | Kinematics, Jacobians, inverse dynamics — the inner loop of optimization | **tier 1 only**, kept for its ~10× speed over Drake at this tier |
| Verification-grade dynamics | **Drake** | Rigorous multibody, contact, static-equilibrium proofs | **tier 3 only** — never the inner loop |
| Fast contact simulation | **MuJoCo** | Thousands of rollouts; the coarse filter before anything expensive | tier 2 |
| Aerodynamics (only if in scope) | **OpenRocket** + **ThrustCurve.org API** + **AeroSandbox** | None of the above three model fluid flow — don't hand-roll aero | out of scope for v1/v2 unless the product pivots to aero robots |
| Numeric optimization | **SciPy → pymoo (NSGA-II) → Ax/BoTorch → JAX-surrogate** | Progression by evaluation cost, not by sophistication for its own sake | JAX cannot autodiff through CAD (§5) — three legitimate JAX uses only |
| Backend | **FastAPI** | Orchestration; every long op returns a job id, nothing runs CAD inline | unchanged |
| Job queue | **Dramatiq** (or Celery) on **Redis** | Retries, dead-letter, heartbeats already solved | Phase 2 — see §6 |
| Database | **PostgreSQL** | Designs, immutable revisions, evaluations, criteria results, catalogue | Phase 2 — see §6 |
| Object storage | **MinIO** (S3-compatible) | STEP/STL/GLB/URDF/MJCF/USD artifacts | Phase 2 — see §6 |
| Orchestration | **LangGraph** + `langgraph-checkpoint-postgres` | Durable, resumable, replayable multi-agent graph; `interrupt()` for hardware gates | Phase 2 only (§7) |
| Frontend | **React + TypeScript** | Application shell | unchanged |
| 3D viewer | **Viser** (interactive joint view) + `<model-viewer>` (GLB cards) | Off-the-shelf; custom Three.js shrinks to the coverage-matrix overlay only | unchanged |
| Robot interop formats | **URDF, MJCF, USD** exported from IR | IR is canonical; these are exporters, not sources of truth | dropped SDF/Gazebo — redundant with MuJoCo+Drake, no added capability |
| Deployment | **Docker Compose**, one machine | No cloud services; local-first is a hard constraint | Phase 1 skips even this — see §6 |
| GPU | None required for v1/v2 core | CAD and MuJoCo are CPU-bound | Isaac Sim is optional, gated, skippable entirely (§8) |
| Real-hardware bridge | **ROS 2** | Only once simulation needs to drive an actual controller | not before Phase 3, and only if HIL is funded |

---

## 2. Robot IR — non-negotiable shape

Topology is data, not code: a quadruped and a rover differ by IR document, not by
Python. Two rules that came from a prototype failure and must not be relaxed:

- **`CatalogueParam`, never a free scalar, for any real part.** An optimizer will
  exploit any variable not tied to a purchasable component — a continuous shoulder-gear
  ratio converged on 7.5:1, which nobody manufactures. Discrete catalogue keys only.
- **`Quantity` requires `Provenance`.** No bare floats anywhere near a physical
  constant. `CONFIRMED | INFERRED | ASSUMED | MEASURED` — see §5.

```python
class CatalogueParam(BaseModel):
    kind: Literal["catalogue"] = "catalogue"
    value: str          # catalogue key, e.g. "planetary_13.73"
    catalogue: str       # e.g. "stepper_motors"

class Provenance(BaseModel):
    status: Literal["CONFIRMED", "INFERRED", "ASSUMED", "MEASURED"]
    source: str
    note: str = ""
```

Geometry generators are a **registry** (`tube`, `plate`, `bracket`, ...), never a
hierarchy. Adding a robot type means adding generators and criteria — never touching
the IR schema or the harness. Revisions are **immutable and append-only**; the trace of
which change fixed which criterion is the only dataset worth having, and it's the
training data for a later surrogate model.

---

## 3. Physics stack — resolved, not open

Three engines is real overlap, so the roles are fixed rather than left as a running
decision:

```
tier 0  analytic     mount fit, reach, static margin        <1 ms   every candidate
tier 1  Pinocchio    torque budgets, CoM, workspace          ~1 ms   every candidate
tier 2  MuJoCo       contact, settling, tip-over             ~1 s    survivors of tier 0/1
tier 3  Drake        equilibrium proofs, contact-rich proof  ~30 s   designs about to ship
```

Pinocchio earns its place because the inner optimization loop runs it thousands of
times — its ~10× speed over Drake is the difference between an overnight search and a
multi-day one. Drake is never the inner loop. A tier that didn't run is reported
skipped, never silently treated as a pass.

---

## 4. Optimization — by evaluation cost, not sophistication

| Phase | Method | Use when |
|---|---|---|
| v1 | Coordinate descent + catalogue enumeration | Deterministic, debuggable, no tuning |
| v1.5 | `pymoo` (NSGA-II) / `scipy` DE | Multi-objective, mixed discrete/continuous |
| v2 | Bayesian optimization (Ax/BoTorch) | Each evaluation costs seconds — sample efficiency matters most |
| v3 | Surrogate network + JAX | Only once thousands of stored evaluations exist to train on |

**JAX cannot be bolted onto the CAD pipeline** — OpenCascade booleans aren't
differentiable. Its three legitimate uses: trajectory optimization on a fixed design
(MuJoCo MJX), a differentiable surrogate trained on real evaluations and always
re-verified by the real harness, and closed-form analytic criteria that are already
differentiable. Optimize the physical quantity (drag in Newtons), never a normalized
coefficient — a coefficient-only objective inflated body diameter and *raised* actual
drag while improving Cd.

---

## 5. Provenance ladder

`CONFIRMED` (manufacturer drawing) → `INFERRED` (derived/secondary source) → `ASSUMED`
(chosen by us) → `MEASURED` (instrumented hardware — the only status that outranks
CONFIRMED, and only reachable via Phase 3 HIL). CI fails if a `CONFIRMED` entry lacks a
resolvable source. **Vendor CAD is for visuals only** — never cut a mating feature from
community/downloaded CAD; bolt patterns and shaft diameters come from catalogue
constants, not from a STEP file someone else drew.

---

## 6. Infrastructure — staged, not day-one

Doc 1's original plan puts Postgres + Redis + MinIO + Docker Compose in from the start.
That's the right end state, not the right starting point for a single-user prototype:

- **Phase 1:** SQLite + local filesystem, behind the same storage interface the S3
  driver will later use. The engine is already a pure library with zero I/O (`python -m
  engine.evaluate ir.json`, no infrastructure) — this is what makes the later swap a
  driver change, not a rewrite.
- **Phase 2:** Promote to Postgres (designs/revisions/evaluations/catalogue),
  Redis + Dramatiq (job queue with retries/heartbeats — don't hand-roll this, ad-hoc
  background workers orphan jobs), MinIO (S3-compatible artifact store), all via one
  `docker compose up`.

```sql
CREATE TABLE revisions (
    id uuid PRIMARY KEY,
    design_id uuid NOT NULL REFERENCES designs(id),
    parent_id uuid REFERENCES revisions(id),
    ir jsonb NOT NULL,
    ir_hash text NOT NULL,       -- dedupe identical proposals, enables caching
    author text NOT NULL,        -- 'agent:claude' | 'user:<id>'
    rationale text,
    UNIQUE (design_id, revision_no)
);
```

---

## 7. Agents — one first, split only when earned

Start with **one designer agent** plus the deterministic `evaluate`/`critique` nodes.
Splitting into a full multi-agent graph on day one has no evidence behind it yet — add
complexity when a single prompt genuinely can't hold both design and critique
reasoning, or when per-agent prediction accuracy data says a split would help.

**Phase 2 graph** (LangGraph, Postgres-checkpointed, resumable):
`intent`/`vision` agents synthesize an IR draft → deterministic `SUPERVISOR` node
(never an LLM) routes on Report content and budget → `designer` / `critic` /
`criteria_author` agents propose, deterministic `evaluate`/`probe`/`coverage_verify`
nodes score → `sim_analyst` names failure mechanisms → on all-pass, `sourcing_agent`
builds the BOM.

Every agent emits a structured, Pydantic-validated schema — never free text. A
`Proposal` records its **predicted** effect before evaluation, scored after: wrong
predictions are more informative than vague improvement, and per-agent prediction
accuracy is the real signal for whether an agent is reasoning or guessing (and for
deciding whether a local model is good enough for any given agent — no assumption,
measure it).

BLIND findings (see §8) route to `criteria_author` **before** any further design
search — optimizing a space the harness can't measure is wasted compute by
construction.

---

## 8. Criteria & coverage — the actual moat

A criterion transfers across topologies even when the CAD doesn't — a `mount_fits`
check written for a rover caught the same defect class on a quadruped, unchanged.

- Every criterion must expose a **magnitude**, not just pass/fail — a boolean-only
  criterion is invisible to coverage analysis because its value never moves.
- Prefer ratios and forces over angles and coefficients — an angle-based stability
  criterion registered 1.4% sensitivity to a real dependency; the same relationship as
  a tan ratio registered 5.7%. `atan` saturates and hides real signal.
- **Coverage analysis**: perturb every design variable ±10%, re-evaluate, measure each
  criterion's relative response. Real coverage measures 3–16%; an unmeasured variable
  measures 0.0–0.1% — two orders of magnitude apart, so the threshold isn't delicate.
  `BLIND` = no criterion responds at all (the subsystem is invisible to the system).
  `FRAGILE` = a passing criterion flips to fail (no engineering margin). A BLIND finding
  is a bug in the harness, not the design — no amount of search fixes it; it needs a new
  criterion, and possibly a new design variable, written by a human or `criteria_author`.

---

## 9. Deliberately excluded (and why)

| Excluded | Reason |
|---|---|
| Gazebo / SDF | Redundant — MuJoCo covers fast contact, Drake covers rigorous verification; a third general simulator adds surface area, not capability |
| Cloud CAD (Onshape/Fusion/Zoo text-to-CAD) | Violates the local-first constraint; build123d already gives scriptable local B-rep |
| Full LangChain | Only `langchain-core` is needed for the message/tool primitives LangGraph uses |
| Temporal | A heavy always-on cluster for a local single-box product; Dramatiq suffices |
| CFD (OpenFOAM/SU2) | Only in scope if the product pivots to a genuinely aero-heavy vertical; AeroSandbox covers subsonic estimates until then |
| Isaac Sim (Omniverse) | Requires an RTX GPU on Linux/Windows, no macOS — skip entirely rather than "later" unless that hardware already exists; costs nothing to skip since the pipeline already emits USD |
| ROS 2, ahead of need | Add only once simulation needs to drive real hardware — not a Phase 1 or 2 concern |

---

## 10. Phased roadmap

**Phase 1 — core loop, single agent, local-only** *(the demoable slice)*
IR + geometry registry → mass properties + tier 0/1 → criteria + `evaluate` CLI →
MuJoCo tier → coverage/BLIND analysis → single-agent loop against the deterministic
evaluate/critique nodes → minimal frontend (viewer + revision timeline only).
**Exit test:** a text description converges, unattended, to a design passing every
applicable criterion with margin — entirely on SQLite, no Docker services running.

**Phase 2 — multi-user services, full agent graph**
Promote to Postgres/Redis/MinIO. Split into the full LangGraph supervisor graph, using
Phase 1's prediction-accuracy data to justify where the split actually helps. Build out
the full frontend, including the coverage matrix — the single most important screen,
since it's the only place a user can see what the system isn't checking.

**Phase 3 — verification + real hardware** *(gated, may never trigger)*
Drake verification tier whenever a design needs to be presented as final.
Hardware-in-the-loop only after the closed-loop actuator purchase (Dynamixel-class
servos and/or ODrive/Moteus BLDC — open-loop steppers provide no telemetry and are a
dead end for this). Every HIL run requires a human-approved `interrupt()` before any
actuation, firmware current limits set before first motion, and a physical e-stop —
non-negotiable, not a nice-to-have.

---

## 11. Non-negotiables

1. Only propose parts that exist — catalogue keys, never free scalars for physical components.
2. Never freehand a computed number — inertia, CoM, torque budgets are computed, not guessed.
3. Label provenance — CONFIRMED ≠ INFERRED ≠ ASSUMED ≠ MEASURED.
4. Never cut mating features from vendor/community CAD.
5. Report what was skipped — a tier that didn't run is not a pass.
6. State predictions before evaluating, and report plainly when they're wrong.
7. The engine has zero I/O — if it imports the ORM, the architecture is broken.
8. Revisions are immutable.
