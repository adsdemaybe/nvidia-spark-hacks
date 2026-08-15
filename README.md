# nvidia-spark-hacks

STRUCT prototypes targeting the NVIDIA DGX Spark (GB10 Grace Blackwell, sm_121, aarch64,
CUDA 13, 121 GB unified memory).

One rule runs through all of it:

> **The agent proposes. The harness disposes.** An agent may generate any design or
> critique; it may never declare one valid. Only the deterministic gates return a verdict,
> and when a model and a measurement disagree, the measurement is right and the question is
> why the model was wrong.

## Features

| dir | what |
|---|---|
| [`pcb-ai/`](pcb-ai/) | **F1 — text to PCB.** Spec → parts plan → tscircuit HDL → an L0–L10 gate ladder (lint, compile, place, route, DRC, physics, SPICE, DFM, placement rules, artifacts). Emits Gerbers, a BOM, and a board report for F2. Runs end to end on a local model; a second autorouter (Freerouting) is available as a cross-check. |
| [`cad-generation/`](cad-generation/) | **F2 — text to CAD.** Intent → Robot IR → build123d solids → mass properties → URDF/MJCF → `evaluate()`. Includes `freeform`, which runs model-written build123d. |
| [`cosim/`](cosim/) | **Electromechanical co-simulation.** Couples the board's electrical behaviour to MuJoCo mechanics, gates the rollout, and routes a failure to the side that can fix it. |
| [`rag/`](rag/) | **Docs retrieval** over tscircuit and build123d, so agents write the real API instead of a plausible one. |
| [`realsim/`](realsim/) | F3: one phone video → 8 validated digital-cousin simulation scenes. Start at [`realsim/STATE.md`](realsim/STATE.md). |
| [`ar-vr/`](ar-vr/) | F5: WebXR / hand-tracking teleoperation against the SO-101. |
| [`setup/`](setup/) | Model serving, OpenShell, and box configuration. |

Plans: [`master-plan.md`](master-plan.md) · [`text-to-pcb-plan.md`](text-to-pcb-plan.md) ·
[`electromechanical-cosim-plan.md`](electromechanical-cosim-plan.md)

## The whole stack, end to end

```
        spec (text)
             │
   F1  pcb-ai ──────► circuit JSON ──► board_report ─┐
             │        Gerbers, BOM                   │
             │                                       ▼
   F2  cad-generation ◄──────────────────  enclosure + standoffs
             │  Robot IR, build123d solids, measured mass properties
             ▼
   cosim  MJCF ──► coupled rollout ──► gate ──► route failure to F1 or F2
```

`cosim/tools/full_stack.py` runs all three against one robot and exits non-zero if any
board is unfit. It found two real defects the first time it ran: no rover board had
mounting holes (so every enclosure generated zero standoffs), and the CAD→cosim adapter
had never been run against a real `RobotIR`.

## What works, and what does not

Stated plainly, because a README that only lists intentions is not much use.

**Green.** The three rover boards compile with **0 errors** and every gate runs: physics,
SPICE, DFM, placement rules, KiCad-9 DRC as an independent second opinion. `rag` 15 tests,
`cosim` 39 tests, `cad-generation` 125 tests. The F1→F2→cosim chain runs from one command.

**The AI design loop completes.** Four iterations, every stage, three reviewers, verdicts,
applied revisions, clean exit — against Qwen3-Coder-Next running locally. It had never
finished a single iteration before two fixes landed: a `repetition_penalty`, because the
model was emitting `\t\t\t\n` thousands of times inside a JSON string until it ran out of
budget; and a rule against writing `"` inside a field, because `a standard 0.1" header`
closes the string and discards an otherwise excellent review.

**Not green: no AI-generated board is accepted yet.** The failures have changed class,
which is the encouraging part — from invented APIs (`<connector pins="2">`, which is not
tscircuit) to placement geometry and connectivity: parts off the outline, overlapping pads,
a missing rail. The reviewers describe those accurately and the revise loop applies edits.
The first class needed grounding; the second needs iteration.

**Routing is the live constraint.** Freerouting was tested as a second opinion
(`pcb-ai/tools/freeroute.ts`). On boards both routers complete it is a wash — 5 vs 9 vias
on rover-power, 15 vs 13 on the motor driver, 2 vs 3 on the indicator. On the densest board — the
36-part controller, which tscircuit routes completely — Freerouting reported 0 vias against
83 and a 20% shorter route, having routed **30 of 38 nets**. An incomplete route is cheaper
by construction, so the tool checks coverage before printing cost, prints INCOMPLETE and
exits non-zero. Freerouting is therefore a useful second opinion on small boards and not a
replacement on dense ones.

## Services

Everything is local. Nothing leaves the box.

| port | service | start with |
|---|---|---|
| 8600 | **console** — PCB + CAD viewers in one page | `python ui/console.py` |
| 3246 | CAD viewer (STEP/STL/3MF) | [`ui/README.md`](ui/README.md) |
| 8081 | joint viewer (Viser, one slider per joint) | `python -m cad_api.viewer <ir.json> --host 0.0.0.0` |
| 8100 | vLLM — Qwen3-Coder-Next (NVFP4) | `setup/serve_coder_next.sh` |
| 8101 | vLLM — Nemotron-3-Nano-Omni (vision reviewer) | `setup/serve_nemotron.sh` |
| 8210 | CAD API | `cad-generation/api` |
| 8220 | docs RAG | `rag/` — `PYTHONPATH=src uvicorn docsrag.server:app --port 8220` |
| 8500 | PCB viewer UI | `pcb-ai/tools/serve-ui.ts` |
| 17670 | OpenShell gateway | [`setup/openshell/`](setup/openshell/) |

Offline tools: `pcb-ai/tools/freeroute.ts` (Freerouting cross-check —
`tools/vendor-freerouting.sh` installs it and the Java 25 runtime it needs),
`pcb-ai/tools/kicad-drc.ts` (KiCad 9 second-opinion DRC), `rag/ask.py` (docs from a
terminal).

## Models: why the third choice was the right one

Decode on this box is bound by **memory bandwidth**, not compute. Sampled mid-generation,
the GPU reported **96% "utilization" while performing 0.6% of its bf16 arithmetic** —
`utilization.gpu` counts time-with-a-kernel-resident, not capability used. The governing
quantity is *bytes of weights read per token*.

| model | form | bytes/token | tok/s |
|---|---|---|---|
| Laguna S 2.1 | NVFP4, 118B/8B active | — | 93 GB floor left nothing for anything else |
| Qwen3.8-27B | dense bf16 | ~45 GB | **3.8** |
| Qwen3-Coder-Next | NVFP4, 3B of 80B active | ~2 GB | **30.2** |

3.8 tok/s × 45 GB is 172 GB/s against the GB10's ~221 GB/s — about 78% of peak, so the
dense model was not misconfigured, it was the wrong *shape*. Qwen3-Coder-Next is a
**larger** model that is ~8× faster here, because sparsity and bandwidth-bound decode
compound.

**Footprint decides whether a model fits; bytes-per-token decides whether it is usable.**
Only the second was ever the binding constraint, and it took two model choices to notice.

## Iterating

[`setup/openshell/`](setup/openshell/) — NVIDIA's sandboxed agent runtime, **onboarded and
running agent turns against the local model**:

```
$ nemoclaw my-assistant agent --agent main -m "Reply with exactly: PCB-STACK-OK"
PCB-STACK-OK        model: qwen3-coder-next
```

No cloud key is involved. `onboard --non-interactive` hard-defaults to NVIDIA Endpoints and
validates the key, with no flag to choose otherwise; the interactive menu offers *"Other
OpenAI-compatible endpoint"*, which takes our own vLLM. The sandbox is healthy at
`RestartCount=0`, after 4917 restarts of a crashloop caused by certificates left over from
a previous gateway.

Worth being precise about what it is: **not a training framework** — no train, tune,
dataset or eval subcommand exists in either CLI. It provides non-interactive agent turns,
snapshots and a pluggable inference endpoint, which is what reproducible batches of design
runs are built from. Two things it needed on this box are recorded in that directory: a
port forwarder, because its `vllm-local` provider hardcodes port 8000; and
`--enable-auto-tool-choice --tool-call-parser qwen3_coder` on vLLM, because agents always
send `tools` and vLLM answers 400 to those without it.
