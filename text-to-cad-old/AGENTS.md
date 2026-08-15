# Agent Instructions

**This repo is a research prototype.** Production is specified in `PLAN.md` and built from scratch elsewhere; do not grow this codebase toward production.

This project is a closed-loop robot design system. **You are the agent runtime.**
There is no separate orchestrator deciding what to do next — you read the skills,
run the deterministic scripts, and the scripts decide whether you were right.

## The contract

    YOU      diagnose, propose, critique, and explain trade-offs.
    SCRIPTS  build the CAD, run the physics, and score the criteria.

You may propose anything. You may never declare success. `skills/rover-design/
scripts/evaluate` is the only thing that decides whether a design passes, and it
exits nonzero when it does not. If your reasoning and the harness disagree, the
harness is right and the interesting question is why you were wrong.

## Skills

| Skill | Use for |
| --- | --- |
| `skills/rover-design` | Changing the design: geometry, actuator choice, refinement loop |
| `skills/sim-export` | Emitting and validating URDF / SRDF / USD, inertials, collision |

Read the relevant `SKILL.md` before acting. Its `references/` directory holds the
detail — do not re-derive what is already written down there, and do not
contradict it without saying that you are.

## Non-negotiables

1. **Only propose parts that exist.** Check `scripts/catalogue` first. A
   continuous gear ratio once produced a fictional 7.5:1 gearbox that passed
   every criterion and could not be bought. See
   `skills/rover-design/references/catalogue-discipline.md`.
2. **Never freehand a computed number.** Inertia tensors, centres of mass,
   torque budgets, tip-over margins — compute them, never estimate them.
3. **Label provenance.** CONFIRMED means read off a manufacturer drawing.
   INFERRED means anything else. They are not interchangeable. See
   `skills/rover-design/references/provenance.md`.
4. **Never cut a mating feature from downloaded vendor CAD.** Those models are
   visual placeholders and have been measured wrong in this project already.
   Bolt patterns and bores come from the catalogue constants.
5. **Report what you skipped.** A validation step that did not run is not a
   pass. Say which engines were unavailable and what remains unproven.
6. **State predictions before evaluating.** Name the criterion and the direction
   it should move. Report plainly when the prediction was wrong.

## Environment

The CAD stack lives in a dedicated virtualenv, because installing it into the
system Python upgrades numpy and breaks conda-built packages:

```bash
.venv-cad/bin/python skills/rover-design/scripts/evaluate --current
```

Use `.venv-cad/bin/python` for anything importing `build123d`, `mujoco`, or
`bd_warehouse`. Do not `pip install` into the base environment.

## Layout

    packages/roverkit/     deterministic engine: CAD, sim export, scoring harness
    skills/                agent-facing skills (SKILL.md + references + scripts)
    sim/                   generated simulation artifacts; design.json is the design
    export/                generated STEP/STL per fabricated part
    vendor/                downloaded vendor CAD — VISUALS ONLY, never for mating
    ai/ + graph/           optional headless variant; see below

## Headless variant

`ai/` and `graph/` run the same loop without Claude Code, using LangGraph and a
provider abstraction over Anthropic / OpenAI / Google / Ollama. It exists for
unattended runs. It is not the primary path and duplicates the skills' rules in
prompt form — when the two disagree, `skills/` is authoritative.

## What the harness does not check

Dynamic torque under acceleration, thermal derating, structural deflection,
terrain, cost, manufacturability, wiring, and joint axis *direction*. A wrong
axis sign passes every automated check in this repo. Do not describe a passing
design as verified without naming these gaps.
