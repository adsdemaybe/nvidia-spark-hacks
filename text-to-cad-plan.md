# Robot Design Platform — Implementation Plan
**Audience:** an AI agent implementing this from scratch.
**Status:** design document. Nothing here is built yet except a research prototype
(see *Appendix A*), which exists to prove the loop works and to expose the
mistakes this plan is written to avoid.
---
## 0. The one invariant
> **The agent proposes. The harness disposes.**
>
> An agent may generate any design, any geometry, any critique. It may **never**
> declare a design valid. Only `evaluate()` returns a verdict, and it exits
> nonzero when criteria fail. If agent reasoning and harness output disagree,
> **the harness is right** and the interesting question is why the agent was
> wrong.
Every architectural decision below serves this invariant. If a proposed feature
lets an agent's opinion substitute for a measurement, reject it.
---
## 1. What this system does
A user describes a robot in natural language or supplies a reference image. The
system produces a **manufacturable, physically validated design**: parametric
CAD, simulation models, a bill of materials with real purchasable parts, and a
report stating exactly what was verified and what was not.
The core is a closed loop:
```
  intent ──▶ Robot IR ──▶ CAD ──▶ physics props ──▶ simulation ──▶ real hardware
              ▲                                         │               │
              │                                         ▼               ▼
        agent revision ◀── critique ◀── criteria score ─┴─── measured telemetry
                                                              (HIL, §9.4)
```
**Non-goals for v1.** Real-time control policies, RL training, multi-robot
scenes, manufacturing execution, PLM integration.
---
## 2. The Robot IR — the keystone
Everything depends on this. Get it wrong and the platform can only ever build
one shape of robot.
### 2.1 Why this is the hard part
The prototype hard-coded topology. Wheels were a fixed loop of 4; the arm was
exactly 2 links. Expressing a quadruped required **~430 lines of new
geometry code and an entirely new criteria module** — nothing transferred except
the criteria themselves. That is the failure mode this IR exists to prevent.
**Rule: topology is data, not code.** A quadruped, a rover, and a rocket differ
by their IR document, not by their Python.
### 2.2 Schema (Pydantic v2)
```python
# ir/schema.py
from typing import Literal, Annotated, Union
from pydantic import BaseModel, Field, model_validator
Millimetres = Annotated[float, Field(gt=0)]
UnitVector  = Annotated[tuple[float, float, float], Field()]
class Provenance(BaseModel):
    """Every physical constant carries this. Non-optional. See §8."""
    status: Literal["CONFIRMED", "INFERRED", "ASSUMED", "MEASURED"]
    source: str                      # URL, part number, or derivation
    note: str = ""
class Quantity(BaseModel):
    value: float
    unit: str                        # SI or explicit; never bare numbers
    provenance: Provenance
# ---- Parameters -------------------------------------------------------------
class ContinuousParam(BaseModel):
    kind: Literal["continuous"] = "continuous"
    value: float
    lower: float
    upper: float
    unit: str
    description: str
    @model_validator(mode="after")
    def _in_range(self):
        if not self.lower <= self.value <= self.upper:
            raise ValueError(f"{self.value} outside [{self.lower}, {self.upper}]")
        return self
class CatalogueParam(BaseModel):
    """A discrete choice from a real parts catalogue. NEVER a free scalar.
    An optimiser will exploit any variable not constrained to real parts. The
    prototype's continuous `SHOULDER_GEAR` converged on 7.5:1 — a ratio nobody
    manufactures. Real planetary reductions are 5.18, 13.73, 26.851.
    """
    kind: Literal["catalogue"] = "catalogue"
    value: str                       # catalogue key
    catalogue: str                   # e.g. "stepper_motors"
Param = Annotated[Union[ContinuousParam, CatalogueParam],
                  Field(discriminator="kind")]
# ---- Topology ---------------------------------------------------------------
class Link(BaseModel):
    id: str
    geometry: "GeometryRef"          # named generator + bound params
    material: str                    # key into materials catalogue
    attachments: list["Attachment"] = []
class Joint(BaseModel):
    id: str
    type: Literal["revolute", "prismatic", "continuous", "fixed", "floating",
                  "ball", "planar"]
    parent: str
    child: str
    origin_mm: tuple[float, float, float]
    axis: UnitVector
    limits: "JointLimits | None" = None
    actuator: str | None = None      # catalogue key
    transmission: "Transmission | None" = None
class Attachment(BaseModel):
    """A purchased component mounted to a link. Drives mount-fit checks."""
    catalogue: str
    part: str
    frame_mm: tuple[float, float, float]
    orientation_rpy: tuple[float, float, float] = (0, 0, 0)
class RobotIR(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str
    name: str
    intent: str                      # the user's words, verbatim
    links: list[Link]
    joints: list[Joint]
    params: dict[str, Param]
    criteria: list["CriterionRef"]   # WHICH criteria apply to this robot
    assumptions: list[Provenance] = []
    @model_validator(mode="after")
    def _tree_is_valid(self):
        ids = {l.id for l in self.links}
        for j in self.joints:
            if j.parent not in ids or j.child not in ids:
                raise ValueError(f"joint {j.id} references unknown link")
        children = [j.child for j in self.joints]
        if len(children) != len(set(children)):
            raise ValueError("a link has two parents; IR must be a tree")
        roots = ids - set(children)
        if len(roots) != 1:
            raise ValueError(f"expected exactly one root, found {roots}")
        return self
```
### 2.3 Geometry generators
`GeometryRef` names a registered generator plus bound parameters:
```python
class GeometryRef(BaseModel):
    generator: str                   # "tube", "plate", "bracket", "ogive", ...
    args: dict[str, str | float]     # literals or "$param_name" references
```
Generators are a **registry**, not a hierarchy:
```python
@geometry("tube")
def tube(outer_d: float, wall: float, length: float) -> Part: ...
```
Adding a robot type should mean adding generators and criteria — never editing
the IR schema or the harness.
### 2.4 Versioning
`schema_version` is checked on load. Migrations live in `ir/migrations/` and are
applied in order. **Never mutate a stored IR in place** — every revision is a new
row (see §6).
---
## 3. Architecture
```
┌──────────────────────────────────────────────────────────────┐
│  React + TypeScript · Three.js viewer                        │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + SSE
┌───────────────────────────▼──────────────────────────────────┐
│  FastAPI                                                     │
│    /designs  /revisions  /jobs  /artifacts  /catalogue       │
└─────┬────────────────────────────────────┬───────────────────┘
      │                                    │
┌─────▼──────────┐              ┌──────────▼───────────────────┐
│ PostgreSQL     │              │ Redis  ── job queue          │
│  designs       │              └──────────┬───────────────────┘
│  revisions     │                         │
│  evaluations   │              ┌──────────▼───────────────────┐
│  criteria_runs │              │ Workers (Python)             │
│  catalogue     │              │   cad · sim · optimise · agent│
└────────────────┘              └──────────┬───────────────────┘
                                           │
┌──────────────────────────────────────────▼───────────────────┐
│ ENGINE (pure, no I/O, no network)                            │
│   ir → build123d/OpenCascade → mass properties               │
│      → Pinocchio (kinematics/dynamics)                       │
│      → MuJoCo (fast rollouts) │ Drake (verification)         │
│      → criteria → Report                                     │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ S3-compatible: STEP · STL · GLB · URDF · MJCF · USD · renders │
└──────────────────────────────────────────────────────────────┘
```
**The engine is a library, not a service.** It imports nothing from FastAPI,
Redis, or the ORM. It must be runnable as `python -m engine.evaluate ir.json`
with no infrastructure. This is what makes it testable and what lets the same
code run in CI.
### 3.1 Everything runs locally — hard constraint
The entire stack runs on one machine via `docker compose`. No cloud services.
- **Postgres, Redis** — containers.
- **Object store** — MinIO container. It speaks the S3 API, so storage code is
  written once and survives a future cloud deployment unchanged. A
  plain-filesystem driver is acceptable for the minimal single-user setup.
- **Workers** — host processes or containers. CAD and MuJoCo are CPU-bound; no
  GPU is required anywhere in v1.
- **Frontend** — static build served by the API container.
- **The one external call is the LLM API**, if Claude is used. The fully-local
  alternative is Ollama behind the same provider interface. Expect a real
  quality drop in design reasoning from local models; the architecture must not
  care which is plugged in. Provider is a per-agent run-config field, so you can
  mix: Claude for the designer, a local model for cheap agents. Measure each
  provider's prediction accuracy (§9.3) rather than assuming.
```yaml
# compose sketch
services:
  db:       { image: postgres:16 }
  redis:    { image: redis:7 }
  minio:    { image: minio/minio }
  api:      { build: ./services/api }
  worker:   { build: ./services/worker }   # docker compose up --scale worker=4
  frontend: { build: ./frontend }
  ollama:   { image: ollama/ollama }       # optional, fully-local LLM
```
The provider abstraction is one small module wrapping `anthropic` and the Ollama
client. Do **not** pull in the full LangChain provider zoo — `langchain-core`
only, for the message/tool primitives LangGraph needs.
---
## 4. Physics stack — roles, and a redundancy you should resolve
Three engines is genuine overlap. Assign strict roles or cut one.
| Tool | Role | Do NOT use it for |
|---|---|---|
| **Pinocchio** | Kinematics, Jacobians, RNEA inverse dynamics, CoM/CRBA. Fast, analytic, no contact. | contact, collision |
| **MuJoCo** | Fast contact rollouts. The inner loop of optimisation. Thousands of designs. | authoritative verification |
| **Drake** | Verification tier only. Rigorous multibody, hydroelastic contact, MathematicalProgram, static equilibrium. | the inner loop — too slow |
**Decision to make explicitly:** Drake alone can do everything Pinocchio does.
Pinocchio exists here because it is ~10× faster for repeated inverse dynamics and
trivial to install. If your evaluation budget per design is under ~50 ms,
Pinocchio earns its place. If not, **cut it** and use Drake for both tiers.
**Recommended tiering:**
```
tier 0  analytic       closed-form checks (mount fit, reach, static margin)   <1 ms
tier 1  Pinocchio      torque budgets, CoM, workspace                        ~1 ms
tier 2  MuJoCo         contact, settling, locomotion, tip-over               ~1 s
tier 3  Drake          equilibrium proofs, contact-rich verification         ~30 s
```
Run tier 0–1 on every candidate. Tier 2 on survivors. Tier 3 **only** on designs
about to be presented as final. Record which tiers ran; a skipped tier is not a
pass.
**Optional tier 3 alternative — Isaac Sim (Omniverse/PhysX).** The pipeline
already emits USD with UsdPhysics schemas, which is exactly Isaac's ingestion
format, so this costs almost nothing to support. Use it for PhysX
cross-validation against MuJoCo, photoreal renders, and later Isaac Lab RL.
**Constraint stated plainly: no macOS build; requires an RTX GPU on
Linux/Windows.** It is therefore an optional tier on a dedicated box, gated by
`applies_to`-style capability detection — never the portable core, which stays
MuJoCo. A skipped Isaac tier is reported skipped like any other.
### 4.1 What no engine in this stack does
**Aerodynamics.** None of Pinocchio, MuJoCo, or Drake models fluid flow. A
prototype rocket's drag came from a textbook skin-friction/base-drag correlation
computed *outside* the simulator; the simulator flew it in vacuum. If
aerodynamic robots are in scope, do not hand-roll the aero (the prototype did —
hand-written Barrowman with a sign error, and an INFERRED motor table). Use
existing systems: **OpenRocket** as an engine (mature 6-DOF rocket flight sim,
Barrowman built in, scriptable via `orhelper`, local Java) with motor data from
the **ThrustCurve.org open API** — real measured thrust curves, upgrading motor
provenance from INFERRED to CONFIRMED. For general subsonic aero,
**AeroSandbox** (Python, CasADi-differentiable). Real CFD (OpenFOAM/SU2) is a
tier-4 batch job only if the product genuinely needs it.
Do not let a passing MuJoCo run imply aerodynamic validity.
Also unmodelled by default: thermal derating, structural deflection, fatigue,
EMI, manufacturing tolerance stack-up.
---
## 5. Optimisation — and the JAX constraint
### 5.1 You cannot autodiff through B-rep CAD
OpenCascade is not differentiable. There is no gradient of "chassis mass" with
respect to "wall thickness" flowing through a boolean operation. **JAX cannot be
bolted onto the CAD pipeline.** Any plan that assumes it will fail.
JAX has three legitimate homes here:
1. **Trajectory/control optimisation** on a fixed design (MuJoCo MJX).
2. **Surrogate models** — train a differentiable network on (params → criteria)
   pairs harvested from real evaluations, optimise the surrogate, then verify
   candidates with the real harness. Never ship a surrogate result unverified.
3. **Analytic criteria** that are already closed-form (static margin, torque
   budgets, control authority) — these *are* differentiable and can be optimised
   directly.
### 5.2 Realistic progression
| Phase | Method | Why |
|---|---|---|
| v1 | Coordinate descent + catalogue enumeration | Deterministic, debuggable, no tuning. Converged the prototype rover in 4 steps. |
| v1.5 | `scipy.optimize` (differential evolution, NSGA-II via pymoo) | Handles multi-objective and mixed discrete/continuous. |
| v2 | Bayesian optimisation (Ax/BoTorch) | Sample-efficient when each evaluation costs seconds. **This is the right answer for expensive black-box design**, more than JAX. |
| v3 | Surrogate + JAX | Only once you have thousands of stored evaluations to train on. |
**Multi-objective is the honest framing.** "Minimise drag" alone produces a
finless dart. The prototype did exactly that, and the result was aerodynamically
unstable at −21 calibers. Design is Pareto, not scalar.
### 5.3 Optimise forces, not coefficients
A coefficient is normalised. In the prototype, "minimise Cd" rewarded a *bigger*
rocket: the optimiser inflated body diameter, achieved the best Cd of any design
tried (0.197 → 0.113), and **raised actual drag from 9.5 N to 11.4 N**. Budget
the physical quantity that matters, not its normalised form.
---
## 6. Data model
```sql
CREATE TABLE designs (
    id            uuid PRIMARY KEY,
    owner_id      uuid NOT NULL,
    name          text NOT NULL,
    intent        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
-- Revisions are IMMUTABLE and append-only. Never UPDATE an ir.
CREATE TABLE revisions (
    id            uuid PRIMARY KEY,
    design_id     uuid NOT NULL REFERENCES designs(id),
    parent_id     uuid REFERENCES revisions(id),
    revision_no   int  NOT NULL,
    ir            jsonb NOT NULL,
    ir_hash       text NOT NULL,          -- sha256 of canonical JSON
    author        text NOT NULL,          -- 'agent:claude-x' | 'user:<id>'
    rationale     text,                   -- WHY this change was made
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (design_id, revision_no)
);
CREATE INDEX ON revisions (design_id, revision_no DESC);
CREATE INDEX ON revisions (ir_hash);      -- dedupe identical proposals
CREATE TABLE evaluations (
    id            uuid PRIMARY KEY,
    revision_id   uuid NOT NULL REFERENCES revisions(id),
    engine_ver    text NOT NULL,          -- pinned engine version
    tiers_run     text[] NOT NULL,        -- which tiers actually executed
    tiers_skipped jsonb NOT NULL,         -- {"drake": "not installed"}
    passed        boolean NOT NULL,
    score         double precision NOT NULL,
    started_at    timestamptz NOT NULL,
    duration_ms   int NOT NULL
);
CREATE TABLE criterion_results (
    id            bigserial PRIMARY KEY,
    evaluation_id uuid NOT NULL REFERENCES evaluations(id),
    name          text NOT NULL,
    ok            boolean NOT NULL,
    value         double precision,
    target        double precision,
    note          text,
    tier          text NOT NULL
);
CREATE INDEX ON criterion_results (evaluation_id);
CREATE INDEX ON criterion_results (name, ok);
CREATE TABLE artifacts (
    id            uuid PRIMARY KEY,
    revision_id   uuid NOT NULL REFERENCES revisions(id),
    kind          text NOT NULL,          -- step|stl|glb|urdf|mjcf|usd|png|mp4
    s3_key        text NOT NULL,
    bytes         bigint NOT NULL,
    sha256        text NOT NULL
);
CREATE TABLE catalogue_parts (
    catalogue     text NOT NULL,
    part          text NOT NULL,
    spec          jsonb NOT NULL,         -- every field carries provenance
    PRIMARY KEY (catalogue, part)
);
```
**Why append-only revisions:** the agent loop's value is the *trace* — which
change fixed which criterion, and which prediction was wrong. Overwriting
destroys the only dataset worth having, and it is the training data for the v3
surrogate.
---
## 7. Criteria system
Criteria are the reusable asset. In the prototype, a `mount_fits` criterion
written for the rover transferred **unchanged** to a quadruped and immediately
caught the same defect class on a different topology, before any optimisation
ran. The CAD transferred nothing.
### 7.1 Contract
```python
class Criterion(Protocol):
    name: str
    tier: Literal["analytic", "pinocchio", "mujoco", "drake"]
    applies_to: Callable[[RobotIR], bool]
    def check(self, ctx: EvalContext) -> list[str]:
        """Human-readable failures. Empty list = pass."""
    def metric(self, ctx: EvalContext) -> tuple[float, float]:
        """(value, target). REQUIRED, not optional. See §7.3."""
```
### 7.2 Criteria must be measurable, not boolean
A criterion returning only pass/fail is **invisible to the coverage analysis** in
§7.3, because its numeric value never moves. `metric()` is mandatory.
Also: **how you express a metric determines whether it can be seen.** A lateral
stability criterion expressed as an *angle* registered 1.4% sensitivity to track
width — `atan` saturates near 70°, compressing real dependence into noise. The
same criterion expressed as the **tan ratio** it physically is registered 5.7%.
Prefer ratios and forces over angles and coefficients.
### 7.3 Coverage analysis — the critic
For every design variable, perturb it ±10% of range, re-evaluate, and measure
each criterion's **relative** response.
- Real coverage measures **3–16%**.
- Unmeasured variables measure **0.0–0.1%**.
Two orders of magnitude apart, so a 2% threshold is not delicate.
```text
BLIND    = no criterion responds above threshold  ->  the subsystem is invisible
FRAGILE  = a passing criterion flips to FAIL      ->  no engineering margin
```
**This is the single highest-value component.** Applied to the prototype it found
that 3 of 11 design variables were unmeasured — including gripper aperture, which
is why a robot rated for 937 g payload shipped with a 14 mm jaw opening. It also
found that track width was unmeasured, which nobody had noticed.
**Two failed attempts, recorded so you skip them:**
- v1 asked *"did any criterion move?"* → **false negative.** A gripper variable
  nudges total mass, so mass-coupled criteria twitch and it looks covered.
- v2 asked *"did it move anything beyond the universal mass-coupled set?"* →
  **five false positives.** Wheelbase genuinely drives payload; that is the
  criterion working, not incidental coupling.
- v3 measures **magnitude**. Correct.
### 7.4 BLIND findings cannot be fixed by search
A search loop moves values inside a space the criteria define. If a subsystem is
unmeasured, every value scores identically — there is no gradient. **A BLIND
finding is a work item against the harness, not the design.** It requires a human
or agent to write a new criterion, then confirm coverage rose above threshold.
Automate the scaffolding and the verification; the judgement of *what correct
means* is not automatable.
Adding a criterion frequently forces adding a **design variable**, or the
criterion is unsatisfiable and the loop stalls instead of converging.
---
## 8. Provenance — non-negotiable
Every physical constant is `CONFIRMED` (read off a manufacturer drawing),
`INFERRED` (derived or secondary source), `ASSUMED` (chosen by us), or
`MEASURED` (obtained from instrumented hardware, §9.4 — the only status that
outranks CONFIRMED). These are **not interchangeable** and the distinction must
survive into the final report.
Enforcement:
1. `Quantity` requires `Provenance`. No bare floats in the catalogue.
2. CI fails if any `CONFIRMED` entry lacks a resolvable `source`.
3. The design report groups every assumption by status.
**Vendor CAD is for visuals only.** A downloaded NEMA17 STEP measured body
32.65 mm / shaft 20.1 mm — a short 34 mm-class motor, not the 40 mm part it
stood in for. **Never cut a mating feature from community CAD.** Bolt patterns,
pilot bores, and shaft diameters come from catalogue constants.
Known-bad secondary sources encountered: NopSCADlib gives the Raspberry Pi 4
mounting hole as Ø3.0; the official drawing says Ø2.7.
---
## 9. Services
### 9.1 FastAPI
```
POST   /designs                        create from intent (+ optional image)
GET    /designs/{id}
GET    /designs/{id}/revisions
POST   /designs/{id}/revisions         explicit param override
GET    /revisions/{id}
GET    /revisions/{id}/evaluation
GET    /revisions/{id}/artifacts
POST   /revisions/{id}/evaluate        enqueue → job id
POST   /revisions/{id}/critique        enqueue coverage analysis
POST   /designs/{id}/optimise          enqueue loop; body: budget, objectives
GET    /jobs/{id}                      status
GET    /jobs/{id}/events               SSE progress stream
GET    /catalogue/{name}
```
Rules: every long operation returns a job id — no request runs CAD inline.
Artifacts are served as presigned S3 URLs, never proxied. The API imports the
engine as a library and never reimplements a criterion.
### 9.2 Workers
| Queue | Work | Concurrency |
|---|---|---|
| `cad` | geometry, mass properties, exports | CPU-bound, 1 per core |
| `sim` | MuJoCo rollouts | CPU-bound, memory-heavy |
| `verify` | Drake | slow, small pool |
| `optimise` | orchestrates the loop, enqueues children | I/O-bound, many |
| `agent` | Claude calls | I/O-bound, rate-limited |
**Do not hand-roll the worker loop.** Use an existing task system on Redis —
**Dramatiq** (simple, local, retries/dead-letter built in) or Celery. The
prototype's ad-hoc background processes produced orphaned jobs and false
"running" states more than once; that is exactly the failure class these systems
already solve.
Requirements: idempotent by `(ir_hash, engine_version)` — identical input returns
the cached evaluation. Hard timeouts per tier. Progress via Redis pub/sub → SSE.
**A worker crash must never leave a job "running" forever** — heartbeat with
reaper.
### 9.3 Orchestration — LangGraph multi-agent pipeline
**Why LangGraph now, when the prototype abandoned it.** The prototype's
LangGraph path was redundant because an interactive Claude Code session was the
runtime — a human drove the loop, and duplicating the skills' rules in prompt
form created two sources of truth. A production service has no human driving it.
It needs an orchestrator that is programmatic, durable, resumable, and
auditable. That is LangGraph's job. **The invariant does not change**: agents
propose, deterministic nodes dispose. The graph makes the loop headless; it does
not give agents authority.
#### Principles
1. **Deterministic work is a plain node, never an agent.** `evaluate`,
   `critique`, `export`, system-ID — function nodes calling the engine.
2. **Routing comes from Report content via conditional edges** — never from an
   agent's self-assessment. An agent cannot route the graph to "done".
3. **State checkpoints to Postgres** (`langgraph-checkpoint-postgres`, local).
   Kill the process mid-run; it resumes. The checkpoint log *is* the audit
   trail and the replay mechanism.
4. **Structured outputs only.** Every agent emits a Pydantic-validated schema;
   a parse failure is a bounded retry, not a crash and not free-text parsing.
5. **Human gates via `interrupt()`** — mandatory before any hardware actuation
   (§9.4), optional before expensive tiers (Drake, long optimisations).
#### The graph
```text
                 ┌────────────┐    ┌────────────┐
   text ───────▶ │ intent     │    │ vision     │ ◀─── image
                 │ agent      │    │ agent      │
                 └─────┬──────┘    └─────┬──────┘
                       └───────┬─────────┘
                               ▼
                       [ir_synthesise]           deterministic: merge, validate,
                               │                 clamp to catalogue + bounds
                               ▼
                ┌──────────────────────────────┐
                │          SUPERVISOR          │  deterministic policy node:
                │  routes on Report + budget   │  NOT an LLM
                └──┬─────────┬────────┬────────┘
                   ▼         ▼        ▼
              designer    critic   criteria_author        (agents)
                   │         │        │
                   ▼         ▼        ▼
              [evaluate]  [probe]  [coverage_verify]      (deterministic)
                   │         │        │
                   └────┬────┴────────┘
                        ▼
                  sim_analyst          (agent: reads rollout traces, names the
                        │               physical mechanism behind a failure)
                        ▼
                  ┌───────────┐  all-pass + margin
                  │  policy   │──────────────▶ [export_bundle] ──▶ sourcing_agent
                  └─────┬─────┘                                        │
                        │ hardware phase — interrupt(): human gate     ▼
                        ▼                                     BOM + final report
                  [hil_runner] ──▶ hil_analyst ──▶ [calibrate] ──▶ SUPERVISOR
```
#### The agents
Each has its own system prompt, low temperature, and a structured output schema.
The rightmost column is enforced by the graph, not by prompt politeness.
| Agent | Reads | Emits | Structurally cannot |
|---|---|---|---|
| `intent` | user text, catalogue index | RobotIR draft | invent parts (schema forces catalogue keys) |
| `vision` | reference image | proportional spec, topology, `undetermined[]` | emit absolute millimetres |
| `designer` | IR, last Report, catalogue, last N (proposal→result) pairs | `Proposal` | mark anything as passing |
| `critic` | probe results from `[probe]` | prioritised FRAGILE/BLIND triage | soften a deterministic verdict |
| `criteria_author` | a BLIND finding, criterion templates | new criterion code + metric | ship without `[coverage_verify]` proof |
| `sim_analyst` | rollout traces, contact/torque time series | failure-mechanism narrative, suspect params | overrule the Report |
| `hil_analyst` | real telemetry vs sim prediction | discrepancy table, calibration suggestions | actuate hardware |
| `sourcing` | converged IR | BOM with provenance + availability | substitute parts silently |
```python
class Proposal(BaseModel):
    changes: dict[str, str | float]
    mechanism: str      # the PHYSICAL reason this should work
    prediction: dict[str, Literal["increase", "decrease", "unchanged"]]
    risks: list[str]    # what currently passes that this might break
```
**Predictions are recorded before evaluation and scored after.** A wrong
prediction is more informative than a vague improvement, and per-agent,
per-provider prediction accuracy is the only real measure of whether an agent is
reasoning or guessing — and the basis for deciding where a local model is good
enough (§3.1).
Prompt context is the IR, the last report, the catalogue, the criteria docs, and
recent (proposal → result) pairs. **Not** the CAD source.
#### Supervisor policy (deterministic, in code)
- **Stop**: all criteria pass with ≥15% margin on every metric; or budget
  (evaluations / wall-clock / tokens) exhausted; or the user interrupts.
- **Oscillation**: an `ir_hash` seen twice → halve continuous steps and hand the
  designer the full history with an explicit "you have been here" note.
- **BLIND routes before optimisation.** A BLIND finding goes to
  `criteria_author` *before* any further design search — optimising a space the
  harness cannot measure is wasted compute by construction (§7.4).
- **FRAGILE routes to the designer** with the flipping variable named.
### 9.4 Hardware-in-the-loop — feedback from the real robot
Sim-only loops validate a design against a *model* of physics. HIL validates the
model itself, and it extends the provenance ladder (§8) with a fourth, highest
status: **MEASURED** — obtained from instrumented hardware, displacing an
ASSUMED value.
What real hardware provides that no simulator can:
- actual joint friction and damping (coast-down test)
- true actuator torque under load — motor **current** is the torque proxy, and
  closed-loop drivers report it continuously
- backlash as a measured dead-band, not a catalogue figure
- battery sag under load; thermal derating over minutes of holding torque
  (the failure mode the sim explicitly does not model)
- assembly truth: the bolt pattern either fits or it does not
**Prerequisite — an actuator decision to verify (see §15):** the prototype's
step/dir stepper motors are open-loop. No encoder, no usable current telemetry.
They physically cannot provide component-derived feedback. HIL requires
closed-loop actuators (Dynamixel-class smart servos and/or ODrive/Moteus BLDC on
CAN). This changes the BOM and the motor catalogue.
#### Deterministic HIL tests (scripts, not agents)
| Test | Procedure | Yields (MEASURED) |
|---|---|---|
| `coast_down` | spin joint to ω, cut torque, log decay | friction + damping via least squares |
| `step_response` | position step, log trajectory | effective inertia, controller gains |
| `stall_margin` | ramp current to a capped, torque-limited stall | true torque constant |
| `settle` | the sim settle criterion executed physically | real tip margin |
| `repeatability` | identical command ×10, measure variance | backlash + compliance |
Each run produces a telemetry artifact (Parquet, in MinIO) plus discrepancy
rows: `(criterion, sim_value, measured_value, relative_error)`. Errors above
threshold route to `[calibrate]`, a deterministic node that writes MEASURED
values over the corresponding ASSUMED parameters, after which the supervisor
re-runs the affected tiers. **This is the sim-to-real gap closing by
measurement, not by tuning until it looks right.**
#### Safety — non-negotiable
- `interrupt()` human approval before **any** actuation, every run.
- Firmware current limits set below thermal ratings before first motion; HIL
  scripts refuse to run if limits are unset.
- Physical e-stop wired. First runs torque-limited. Drive tests with the robot
  on blocks.
---
## 10. Frontend
React + TypeScript + Three.js.
| View | Contents |
|---|---|
| Design | 3D viewer, criteria panel, revision timeline |
| Revision diff | param deltas, criteria deltas, agent rationale |
| Critique | coverage matrix — variables × criteria, BLIND cells highlighted |
| Catalogue | parts browser with provenance badges |
| Jobs | live SSE progress |
Viewer: **embed an existing viewer, do not build a Three.js scene graph from
scratch.** `<model-viewer>` (Google, web component) for GLB display in cards and
reports; **Viser** (Python-native web 3D, from the nerfstudio team) for the
interactive robot view with joint sliders — it serves its own frontend and
speaks to the engine directly, replacing weeks of custom Three.js. Custom
Three.js code is a last resort for the coverage-matrix overlay only.
Format: **GLB, not STL.** GLB carries materials and per-part hierarchy; STL is
one anonymous triangle soup. Generate GLB per revision as a first-class artifact.
Use Draco compression; lazy-load parts; the viewer must not download 50 MB to
show a thumbnail.
The coverage matrix is the most important screen in the product. It is the only
place a user can see *what the system is not checking* — and that is the thing
that will hurt them.
---
## 11. Milestones
Each milestone ends with a **falsifiable** test.
### M1 — IR + geometry registry (2 weeks)
Pydantic schema, tree validation, migrations, geometry registry, IR → STEP/STL.
**Done when:** a rover, a quadruped, and a fixed-wing airframe are all expressed
as IR documents with **zero new Python** beyond geometry generators.
*This is the milestone that proves the architecture.* If it fails, stop and
redesign the IR — everything downstream inherits the flaw.
### M2 — Mass properties + tier 0/1 (1 week)
OCC volume integration → mass, CoM, inertia tensor. Pinocchio model build.
**Done when:** a hand-computable case (uniform box) matches analytic inertia to
1e-9, and `A + B ≥ C` holds for every link in every fixture.
> Trap, already hit: OCC's `MatrixOfInertia()` is referenced to the **centre of
> mass**, not the origin. Treating it as origin-referenced and applying a
> parallel-axis shift subtracts the offset twice and yields negative
> eigenvalues. Verify with the box test before trusting anything.
### M3 — Criteria + evaluate (2 weeks)
Criterion protocol, registry, `applies_to` dispatch, Report, CLI.
**Done when:** `python -m engine.evaluate ir.json` returns a scored report and
exits nonzero on failure, with no database or network.
### M4 — MuJoCo tier (2 weeks)
MJCF/URDF export, floating base, primitive collision, joint drives.
**Done when:** a robot settles upright, drives/steps under actuation, and the
model's total mass equals the IR's computed mass to 0.1%.
> Traps: MuJoCo welds a URDF root to the world unless you add a free joint —
> and silently drops the base link's mass when you don't. Convex-hulling a
> hollow chassis fills it solid. `<mimic>` is ignored; coupled joints need
> equality constraints.
### M5 — Critique + coverage (1 week)
Perturbation analysis, magnitude thresholds, BLIND/FRAGILE reporting.
**Done when:** deliberately removing a criterion causes its variables to be
reported BLIND, and the coverage matrix renders.
### M6 — Services (3 weeks)
FastAPI, Postgres, Redis, workers, S3, SSE.
**Done when:** an evaluation survives a worker kill −9 and resumes; identical IR
hashes return cached results.
### M7 — LangGraph orchestration (3 weeks)
Graph, Postgres checkpointer, structured agents, supervisor policy, prediction
scoring, revision lineage.
**Done when:** a deliberately broken design converges headless; killing the
process mid-run and restarting resumes from the checkpoint; the trace replays
end-to-end and shows which change fixed which criterion.
### M8 — Frontend (3 weeks)
Viewer, timeline, coverage matrix, catalogue.
**Done when:** a user drives a full loop without touching a terminal.
### M9 — Drake verification tier (2 weeks)
**Done when:** a design that passes MuJoCo but fails Drake is found and
explained. If you never find one, your MuJoCo models are probably too forgiving —
that is itself a finding.
### M10 — Hardware-in-the-loop (3 weeks + hardware lead time)
Closed-loop actuator bench (one joint is enough to start), telemetry pipeline,
coast-down + step-response + stall tests, `[calibrate]` node, MEASURED
provenance.
**Done when:** a friction value measured on the bench replaces an ASSUMED value,
the affected tiers re-run automatically, and at least one sim result changes as
a consequence. Gated on the actuator purchase decision in §15.
---
## 12. Non-negotiables
1. **Only propose parts that exist.** Catalogue keys, never free scalars for
   physical components.
2. **Never freehand a computed number.** Inertia tensors, CoM, torque budgets,
   margins — compute them. A prototype MJCF freehanded `diaginertia` and
   understated roll inertia by **4.8×**, making the vehicle spin implausibly.
3. **Label provenance.** CONFIRMED ≠ INFERRED ≠ ASSUMED.
4. **Never cut mating features from vendor CAD.**
5. **Report what was skipped.** A tier that did not run is not a pass.
6. **State predictions before evaluating**, and report plainly when wrong.
7. **The engine has no I/O.** If it imports the ORM, the design is broken.
8. **Revisions are immutable.**
9. **A feature must land on material.** The CAD kernel treats "cut a hole outside
   the part" as a no-op, so an oversized motor produces a valid solid with no
   holes and a passing geometry check. Three separate searches shipped a design
   whose motor could not be bolted on. Check that fastener patterns fall within
   the face they mount to.
---
## 13. Risks
| Risk | Severity | Mitigation |
|---|---|---|
| IR cannot express a new topology | **critical** | M1 gate with three topologies |
| Criteria coverage gaps ship broken designs | **critical** | Critique in CI; block release on BLIND |
| Drake install/build burden | high | Docker image; make tier 3 optional |
| CAD eval too slow for optimisation | high | Cache by `ir_hash`; tier 0/1 first; parallel workers |
| Agent games a weak criterion | high | Coverage analysis; adversarial critic; prediction scoring |
| JAX assumed to differentiate CAD | high | §5.1 — surrogate only |
| No CFD for aero robots | medium | Scope out, or tier-4 OpenFOAM |
| Catalogue data wrong | medium | Provenance enforcement; CI source checks |
| Local LLM too weak for designer/critic | high | Per-agent provider mixing (§3.1); score prediction accuracy per provider; keep Claude for the designer |
| HIL actuation incident | **critical** | §9.4 safety: human gate every run, firmware current limits, e-stop, blocks |
| Open-loop steppers provide no telemetry | high | Closed-loop actuator BOM change (§15) — without it M10 is impossible |
| LangGraph API churn | medium | Pin versions; checkpoint-schema migration test in CI |
---
## 14. Open questions
1. **Cut Pinocchio or Drake?** Both is defensible only if tier-1 latency is
   provably the bottleneck. Measure before committing.
2. **Is CFD in scope?** If yes it changes the worker tier and cost model
   substantially. If no, say so in the product copy.
3. **Multi-objective UX.** Who picks the Pareto point — user, agent, or a weight
   vector? This is a product decision, not a technical one.
4. **Catalogue sourcing at scale.** Manual curation does not scale past a few
   hundred parts; vendor APIs mostly require accounts.
5. **Tolerance and fit.** Real assemblies need tolerance stack-up. Not in v1,
   but the IR should reserve space for it.
6. **Provider mix.** Which agents tolerate a local model? Answer empirically
   from per-agent prediction accuracy (§9.3), not by assumption.
7. **HIL scale.** One instrumented joint on a bench, or a full instrumented
   robot? Start with the joint — most MEASURED values transfer.
---
## 15. Tooling changes — for your verification
Additions to the stated stack, each with the reason and the alternative it beat.
Verify these directions before implementation starts.
| Tool | Why | Alternative considered |
|---|---|---|
| `langgraph` + `langgraph-checkpoint-postgres` | Durable, resumable, replayable multi-agent graph; `interrupt()` gives the mandatory human gate for hardware. Checkpoints in the same local Postgres. | Hand-rolled asyncio state machine — more code, no replay, no ecosystem |
| `langchain-core` **only** | Message/tool primitives LangGraph needs. | Full LangChain — unnecessary surface area |
| Ollama | The fully-local LLM path (§3.1). | Claude-API-only — better reasoning, but one external dependency |
| MinIO | Local S3-compatible store; storage code written once. | Filesystem driver — fine for single-user, loses presigned URLs |
| `pymoo` | NSGA-II for the multi-objective reality of design (§5.2). | SciPy DE alone — single-objective |
| Ax/BoTorch (v2) | Sample-efficient Bayesian optimisation for expensive evals. | Grid/random — wasteful at ~seconds per eval |
| Dynamixel SDK **or** ODrive/Moteus CAN | Closed-loop actuators reporting position/velocity/**current**/temperature — the prerequisite for any component-derived feedback. Dynamixel for arm joints (daisy-chain, best telemetry-per-dollar); ODrive/Moteus for wheels/high-torque. | Keeping open-loop steppers — no telemetry, HIL dead on arrival |
| `pyserial` / `python-can` | HIL transport. | Vendor GUIs — not scriptable |
| `rerun-sdk` | Local-first telemetry + 3D trace viewer for HIL and sim rollouts. | Foxglove (heavier), matplotlib (no shared timeline) |
| `pyarrow` (Parquet) | Telemetry artifacts with schema. | CSV — loses types and schema |
Unchanged from your list: Claude, Python, Pydantic IR, build123d/OpenCascade,
Pinocchio (pending the §4 measurement), Drake (verification tier, optional
container), MuJoCo, SciPy, FastAPI, Redis + workers, PostgreSQL, React +
TypeScript + Three.js. JAX deferred to v3 per §5.1.
---
## 16. Buy-before-build audit
**Principle: custom code only where this product is differentiated.** The moat
is four things — the Robot IR, the criteria/coverage system, the provenance
ledger, and the supervisor policy. Everything else should be an existing system
assembled, and every "we'll build X" in this plan was re-audited against that
rule.
### Adopt (replaces planned custom work)
| System | Replaces | Why | Local? |
|---|---|---|---|
| **Dramatiq** (or Celery) | hand-rolled Redis workers (§9.2) | retries, dead-letter, heartbeats already solved; prototype's ad-hoc workers orphaned jobs repeatedly | yes |
| **Viser** + `<model-viewer>` | custom Three.js viewer internals (§10) | joint-slider robot view and GLB display exist off the shelf; custom code shrinks to the coverage overlay | yes |
| **OpenRocket** engine + **ThrustCurve.org API** | hand-rolled Barrowman + INFERRED motor table | mature 6-DOF rocket sim; measured thrust curves fix the worst provenance gap the prototype had | yes (Java) |
| **AeroSandbox** | bespoke aero correlations | maintained, differentiable, documented assumptions | yes |
| **Alembic** | ad-hoc SQL migrations | standard | yes |
| **onshape-to-robot / yourdfpy** ecosystem | custom URDF tooling beyond export | parsing/validation exists; we only own emission | yes |
### Conditional (adopt when the hardware/need exists)
| System | Role | Gate |
|---|---|---|
| **Isaac Sim (Omniverse)** | tier-3 PhysX cross-validation, photoreal render, Isaac Lab RL later | **RTX GPU on Linux/Windows — no macOS.** Consumes our USD as-is. Optional tier, reported skipped when absent |
| **OpenFOAM / SU2** | tier-4 CFD | only if aero robots are truly in scope (§4.1) |
| **CalculiX / scikit-fem** | structural FEA tier | when deflection criteria enter the criteria set |
### Considered and rejected — with reasons
| System | Why not |
|---|---|
| **Onshape / Fusion 360 / Zoo text-to-CAD** | cloud services — violates the local-first constraint (§3.1); build123d already gives scriptable local B-rep |
| **Gazebo (gz-sim)** | redundant: MuJoCo covers fast contact, Drake covers rigorous verification; a third general sim adds surface area, not capability |
| **PyBullet** | superseded by MuJoCo for this use; weaker contact model, stale development |
| **Temporal** | excellent, but a heavy always-on cluster for a local single-box product; Dramatiq suffices |
| **Full LangChain** | §15 — `langchain-core` only |
**The test for any future "let's build X":** is X part of the IR, the criteria,
provenance, or the supervisor? If not, find the existing system first, and
record here why it was or wasn't adopted.
---
## Appendix A — prototype findings
A working research prototype exists (build123d + MuJoCo + a coordinate-descent
loop, three robot topologies). It is **not** the production architecture — its
topology is hard-coded, which is the specific flaw §2 exists to fix. Its findings
are what this plan encodes:
| Finding | Consequence for this plan |
|---|---|
| Continuous gear ratio → unbuyable 7.5:1 gearbox | §2.2 `CatalogueParam` |
| 3 of 11 variables unmeasured; 14 mm gripper on a 937 g payload robot | §7.3 coverage analysis |
| `mount_fits` caught the same defect on a different topology | §7 criteria are the reusable asset |
| Motor bolt circle 6.6 mm outside the part; holes silently not cut | §12.9 |
| Minimising drag alone → aerodynamically unstable dart (−21 calibers) | §5.2 multi-objective |
| Minimising Cd raised actual drag 9.5 N → 11.4 N | §5.3 optimise forces |
| 4 radial thrusters → rank-2 control, zero roll authority | control-authority criteria |
| OCC inertia is COM-referenced | §M2 trap |
| Freehanded inertia understated roll 4.8× | §12.2 |
| Expressing stability as an angle hid a real dependence | §7.2 |
| Wheelbase fix took payload 204 g → 696 g in 4 steps | coordinate descent is a fine v1 |
**The meta-lesson:** every one of these was found by a *measurement*, and most
were invisible to reasoning alone. Build the measurement infrastructure first.
