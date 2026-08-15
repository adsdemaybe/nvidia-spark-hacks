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
| [`cad-generation/`](cad-generation/) | **F2 — text to CAD.** Intent → Robot IR → build123d solids → mass properties → URDF/MJCF → `evaluate()`. Includes `freeform`, which runs model-written build123d; `sourcing`, which builds the parts catalogue from distributor APIs with citations; and the electronics subsystem that specifies F1's boards and reads their measured facts back. |
| [`cosim/`](cosim/) | **Electromechanical co-simulation.** Couples the board's electrical behaviour to MuJoCo mechanics, gates the rollout, and routes a failure to the side that can fix it. |
| [`rag/`](rag/) | **Docs retrieval** over tscircuit and build123d, so agents write the real API instead of a plausible one. |
| [`realsim/`](realsim/) | F3: one phone video → 8 validated digital-cousin simulation scenes. Start at [`realsim/STATE.md`](realsim/STATE.md). |
| [`ar-vr/`](ar-vr/) | F5: WebXR / hand-tracking teleoperation against the SO-101. |
| [`setup/`](setup/) | Model serving, OpenShell, and box configuration. |

Plans: [`master-plan.md`](master-plan.md) · [`text-to-pcb-plan.md`](text-to-pcb-plan.md) ·
[`electromechanical-cosim-plan.md`](electromechanical-cosim-plan.md)

## The whole stack, end to end

```
        intent (text)
             │
             ▼
   F2  cad-generation ── Robot IR ─┬─► board-spec.md + envelope.json ──┐
             │                     │   rails, budgets, bay, connectors  │
             │  build123d solids   │                                    ▼
             │  computed mass      │                          F1  pcb-ai
             │  properties         │                            L0–L10 gates
             │                     │                            Gerbers, BOM
             │                     └◄── MEASURED facts ◄─────────  board GLB
             │                          mass, CoM, dissipation,   circuit JSON
             ▼                          outline, gate status
   cosim  MJCF ──► coupled rollout ──► gate ──► route failure to F1 or F2
```

Both directions are files on disk. The two codebases are never linked — `pcb-ai`
is TypeScript, `cad-generation` is Python, and the contract is
`pcb-ai/src/cad/contracts.ts` paired with `cad-generation/api/src/cad_api/contracts.py`,
which change in the same commit.

`cosim/tools/full_stack.py` runs all three against one robot and exits non-zero if any
board is unfit. It found two real defects the first time it ran: no rover board had
mounting holes (so every enclosure generated zero standoffs), and the CAD→cosim adapter
had never been run against a real `RobotIR`.

## What works, and what does not

Stated plainly, because a README that only lists intentions is not much use.

**Green.** The three rover boards compile with **0 errors** and every gate runs: physics,
SPICE, DFM, placement rules, KiCad-9 DRC as an independent second opinion. `rag` 15 tests,
`cosim` 39 tests, `cad-generation` 210 engine tests + 41 API tests. The F1→F2→cosim chain
runs from one command.

**The loop closes both ways.** The robot side now emits the board's specification —
rail voltages and current budgets computed from the actuators actually on each rail,
the bay it must fit, connector placement as rules `pcb-ai` enforces at L3 — and reads
the run directory back as MEASURED-class facts. A board that fails DRC fails the robot
by construction: `board_gate_passed` is an ordinary criterion, and `evaluate()` takes no
argument about which failures to ignore.

Three things that found real defects while being built, which is the only reason to
list them:

- The envelope emitter is validated against the *contract file* rather than a copy of
  its shape. On its first run it caught keepouts being emitted as zero-area rectangles —
  schema-valid, and read downstream as "this board has no keepouts".
- Coverage analysis reported the rail voltage as **BLIND**: a ±10% perturbation moved no
  criterion at all, because nothing checked that the rail could run the motor bolted to
  it. That is a 12 V servo browning out on a pack that sags to 9.4 V, with torque,
  current and geometry all individually fine. `actuator_voltage_in_range` exists now.
- The same analysis reported `rail_margin` as blind to motor choice — which would have
  meant the whole electronics integration was decorative. It was a false finding: the
  swap searched only within one catalogue, and all three stepper entries share a 1.7 A
  rated current. Searching across actuator catalogues, the response is 100%.

**Not green: the catalogue predates its own sourcing pipeline.** `engine/sourcing/`
implements §6 — distributor providers, a content-addressed cache, a librarian whose
extractions cannot construct without a document-hash citation and cannot be confirmed by
an agent, and a model-ingest mass cross-check that quarantines a mis-scaled STEP before
it skews every CoM downstream. But the 85 values already in the catalogue were
transcribed, not extracted. `python -m engine.sourcing debt` lists them; the 26 ASSUMED
ones come first, headed by three battery internal resistances that nothing else in the
model is as sensitive to.

**The AI design loop completes.** Four iterations, every stage, three reviewers, verdicts,
applied revisions, clean exit — against Qwen3-Coder-Next running locally. It had never
finished a single iteration before two fixes landed: a `repetition_penalty`, because the
model was emitting `\t\t\t\n` thousands of times inside a JSON string until it ran out of
budget; and a rule against writing `"` inside a field, because `a standard 0.1" header`
closes the string and discards an otherwise excellent review.

**Not green: no AI-generated board is accepted yet.** The failures have changed class,
which is the encouraging part — from invented APIs (`<connector pins="2">`, which is not
tscircuit) to placement geometry and connectivity: parts off the outline, overlapping pads.
The reviewers describe those accurately and the revise loop applies edits.

**One reason the loop could not converge is worth knowing.** Tracing a run where errors
went 8 → 12, the root-cause error was byte-identical before and after: `Could not create
pinheader "J1" … pinLabels`. The tscircuit docs show `pinLabels={["VCC", "GND"]}` and the
compiler rejects exactly that — pin keys are 1-based, so an array's index 0 is invalid and
the whole component fails to create. Measured: array → 3 errors and 0 parts, object
`{1:…, 2:…}` → 0 errors. `chip` behaves identically and its page documents the array form
too, so this is the documented shape being wrong rather than one page being stale.

That made it *unfixable* rather than merely wrong, because the loop is a closed circle: the
designer reads the retrieved docs and writes the array form, the compiler rejects it, the
reviewer reads the same docs and its work order says `pinLabels=["3.3V","GND"]`, the model
applies that faithfully, and the error returns unchanged. The uncomfortable corollary is
that the docs RAG feeds this — grounding a model in real documentation is right, and it
inherits whatever the documentation gets wrong. **Retrieval is not a correctness oracle.**

A lint rule now catches it before compile, which is the only place in the chain that
outranks upstream docs. After it: the error appears once in iteration 0, the model corrects
it, and it never returns — where previously it recurred in every iteration.

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
| 8610 | **studio** — prompt box that runs the whole pipeline | `python ui/studio.py` |
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
