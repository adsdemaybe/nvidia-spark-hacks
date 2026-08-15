# rover-design

> **STATUS: research prototype / proof-of-capability. Not the production system.**
> Production is a greenfield rebuild specified in [PLAN.md](PLAN.md). Do not
> extend this repo toward production — its topology is hard-coded, which is the
> exact flaw PLAN.md §2 exists to fix. What carries over is listed below.

Closed-loop parametric robot design: a rover with a 3-axis arm that is
refined against a physics harness until every success criterion passes.

Structured as a **library of agent skills** — the agent reads `SKILL.md`, runs
deterministic scripts, and the scripts decide whether it was right.

## Skills

| Skill | Summary | Source |
| --- | --- | --- |
| Rover Design | Diagnose, propose, and verify design revisions against measured criteria. | [skills/rover-design](skills/rover-design/SKILL.md) |
| Sim Export | Emit and validate URDF / SRDF / USD with real inertials and primitive collision. | [skills/sim-export](skills/sim-export/SKILL.md) |

## Quick start

```bash
.venv-cad/bin/python skills/rover-design/scripts/catalogue
.venv-cad/bin/python skills/rover-design/scripts/evaluate --current
.venv-cad/bin/python skills/rover-design/scripts/refine
.venv-cad/bin/python skills/sim-export/scripts/export
```

`evaluate` exits nonzero when any criterion fails, so the loop composes in a
shell or in CI.

## The contract

The agent proposes; the harness disposes. `scripts/evaluate` builds the real
CAD, exports a URDF, runs MuJoCo, and scores ten criteria. An agent can propose
anything and can never declare success — which is what keeps a confidently wrong
model from costing more than one evaluation.

## What it found

Starting from a hand-written design that failed two criteria, the loop reached
all ten in four accepted steps:

| | before | after |
| --- | --- | --- |
| Front axle | `L/4` (52 mm) | `0.37L` (78 mm) |
| Shoulder | NEMA17, 0.43 N·m | NEMA23 direct drive |
| Payload at full reach | 204 g | 719–755 g |

The wheelbase was the dominant lever — the gripper reaches 277 mm against an
axle 52 mm from centre, a 5:1 lever against a very short base.

## What is not verified

Dynamic torque under acceleration, thermal derating, structural deflection,
terrain, cost, and **joint axis direction** — a wrong sign passes every
automated check here. USD is emitted but not loaded; Isaac Sim is not installed
in this environment.

## Layout

    packages/roverkit/   deterministic engine (CAD, sim export, harness)
    skills/              agent-facing skills
    sim/                 generated URDF/SRDF/USD; design.json holds the design
    export/              generated STEP/STL per fabricated part
    vendor/              downloaded vendor CAD — visuals only, never for mating
    ai/, graph/          optional headless LangGraph variant

## What survives into production (data, not code)

| Carries over | Where it lives now |
| --- | --- |
| Every finding + trap (OCC COM-referenced inertia, MuJoCo freejoint/mimic, convex-hull-on-hollow, mm→m, …) | PLAN.md Appendix A |
| Criteria *logic*: mount-fit, coverage analysis v3 (magnitude, 2% threshold), control-authority rank, static margin as ratio | reimplemented against the production IR |
| Catalogue data with provenance (motor/bearing/servo dims, verified vs inferred) | `packages/roverkit/rover_arm.py` MOTORS + skills references |
| The contract: agents propose, harness disposes; predictions before evaluation | PLAN.md §0, §9.3 |

Everything else — the three loop implementations, the topology-hard-coded CAD,
the skill scripts as written — is disposable scaffolding that proved the loop.

Prior art: structure and conventions follow
[earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad) (MIT).
