---
name: rover-design
description: Closed-loop parametric robot design refinement. Use when creating, revising, tuning, or debugging a parametric robot design against measurable success criteria — chassis and arm geometry, actuator selection, tip-over and payload margin, joint torque budgets, electronics packaging, or when a design must be iterated until it passes in simulation. Use the $sim-export skill for URDF/USD/SRDF emission and inertial computation; use $cad-viewer or a local render for visual review.
---

# Rover Design

Provenance: maintained in this workspace under `skills/rover-design/`. The
installed local skill files are the runtime source of truth.

Use this skill to drive a parametric robot design toward a success condition.
Treat it as constrained optimisation against a physics harness, not as CAD
authoring. The correctness risks are inventing parts that cannot be bought,
optimising a criterion the harness does not measure, and accepting a change on
reasoning instead of measurement.

## Core Rules

1. **The harness decides, never the agent.** `scripts/evaluate` builds the real
   CAD, exports a URDF, runs the physics engine, and scores every criterion. A
   design passes when the report says so. Do not report success from reasoning,
   from a partial check, or from a previous round's result.
2. **Only propose parts that exist.** The shoulder motor must be a key from
   `scripts/catalogue`; the gear ratio must be one of its listed ratios. A
   continuous ratio is how an optimiser invents a part nobody sells — this has
   already happened once in this project and produced a fictional 7.5:1
   gearbox. See `references/catalogue-discipline.md`.
3. **Change one to three variables per round.** If six change and the score
   improves, you have learned nothing about which mattered.
4. **State the mechanism, then the prediction.** Before running an evaluation,
   name the physical quantity the change moves and which criterion should move
   in which direction. A prediction that fails is more informative than a vague
   improvement.
5. **Never freehand a computed number.** Inertia tensors, centres of mass, tip
   -over margins, and torque budgets are computed by the harness. Do not
   estimate them in prose and act on the estimate.
6. **A criterion that is not measured does not exist.** If you are trading
   against something the harness cannot see — cost, manufacturability, thermal
   derating — say so explicitly and do not claim the design is optimal.
7. **Record provenance for every physical constant.** Datasheet-confirmed and
   inferred values are not interchangeable. See `references/provenance.md`.
8. Validate with `scripts/evaluate` before reporting completion, and report
   which checks ran and which were skipped.

## Sim Export Handoff

After a design converges, hand the design off to `$sim-export` to regenerate
`sim/rover.urdf`, `sim/rover.srdf`, and `sim/rover.usda` from the converged
variables. If `$sim-export` is unavailable, say so rather than silently leaving
stale simulation artifacts on disk that no longer match the CAD.

## Workflow

1. Read the current design and the last report: `scripts/evaluate --current`.
2. Diagnose the *root cause* of failure, not the symptoms. Two criteria failing
   for one physical reason is one problem. Compare measured value to target: a
   criterion missing by 2% is tuning, missing by 300% is part selection or
   topology and no tuning will reach it. See `references/criteria.md`.
3. Propose a change. Consult `scripts/catalogue` before naming any part, and
   `references/design-variables.md` for what each variable actually moves.
4. Critique your own proposal adversarially before spending an evaluation:
   does the part exist, is the bound respected, what currently passes that this
   breaks? See `references/critique.md`.
5. Run `scripts/evaluate --design '<json>'` and compare the result against your
   prediction. Say plainly when the prediction was wrong.
6. Keep the change only if the score improved. Otherwise revert and use the
   failed prediction as evidence for the next diagnosis.
7. Repeat until every criterion passes, or until the failure is structural and
   needs a new design variable or criterion rather than another round.
8. Hand off to `$sim-export`, then report residual risk and unverified values.

`scripts/refine` runs steps 1–7 unattended with a deterministic coordinate
search. Use it as a baseline and a fallback, not as a substitute for diagnosis:
it optimises only what is exposed and cannot invent a new variable.

## Commands

Run with the project environment. Treat `python` as an interpreter placeholder;
substitute `python3` or a virtualenv path if bare `python` is unavailable.

```bash
python scripts/evaluate --current
python scripts/evaluate --design '{"AXLE_FRAC": 0.37, "SHOULDER_MOTOR": "23HS22-2804S"}'
python scripts/evaluate --current --format json
python scripts/catalogue
python scripts/catalogue --format json
python scripts/refine --max-iters 12
python scripts/refine --target-payload 0.5 --format json
```

`scripts/evaluate` exits nonzero when any criterion fails, so it composes in a
shell loop or CI. `--format json` emits the full report with every criterion's
measured value, target, and note. A partial design dict is merged over the
current design, so you only pass what you are changing.

## Success Condition

The default success condition is every criterion in `references/criteria.md`
passing simultaneously. It is defined in one place, in code, and is the only
definition — if you find yourself arguing that a design is "good enough" while
the harness disagrees, the correct move is to change the criteria deliberately
and say that you changed them, not to reinterpret the result.

Evaluation is a guardrail, not proof of a working robot. The harness checks
static torque, not dynamic torque under acceleration; rigid bodies, not
compliance or backlash beyond the modelled gearbox term; and a flat plane, not
terrain. A design can pass every check here and still fail on a bench.
