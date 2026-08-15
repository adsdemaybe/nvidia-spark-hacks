# pcb-ai — the pipeline, as built

Quick reference for what runs, in what order, what gates on what, and how to drive it.
`../text-to-pcb-plan.md` is the spec and the argument; this is the map.
`STATE.md` is what is green right now.

---

## The shape

```
  ┌──────── judgement (a model — Laguna, Qwen, or the offline stub) ────────┐
  │  intake → parts/topology → designer → 3 reviewers → chief               │
  └────┬──────────────────────────────────▲──────────────▲──────────────────┘
       │ emits HDL / edit blocks          │ reports      │ verdict + work order
  ─────▼──────────────── determinism (no model) ─────────┴──────────────────
  L0 lint → L1 compile → L3 place → L4 route → L5 DRC → L6 physics
          → L7 SPICE → L8 DFM → L3' placement rules → L9 artifacts
                                                             │
                          revise ◀────── chief ◀─────────────┘
```

**The one invariant: agents propose, the harness disposes.** No agent may declare a
board routable, manufacturable, correctly placed or electrically sound. Tools measure;
agents interpret and revise. Any measured hard failure forces `pass: false` no matter
what the chief concluded.

---

## Stages

| Stage | What it answers | Implementation | Gate |
|---|---|---|---|
| **L0** lint | Is the HDL well-formed? | `eslint-plugin-pcb` (`lint/pcb-plugin.mjs`) | zero errors — compile is skipped if it fails |
| **L1** compile | Does it produce a netlist and geometry? | `@tscircuit/eval` → Circuit JSON | no `_error` elements |
| **L3** place | Do parts fit without colliding? | `pcbPack` + overlap/edge checks | measured −33% area at Jaccard 1.000 |
| **L4** route | Is every net connected? | capacity-autorouter | 100% routed |
| **L5** DRC ×2 | Is the geometry legal? | tscircuit DRC **and** `kicad-cli pcb drc` (`src/kicad/`) | both clean, disagreements filed as pipeline bugs |
| **L6** physics | Does the copper survive the current? | own PCG solvers: thermal, IR drop, current density, IPC-2221 (`src/physics/`) | no hard failure vs budgets |
| **L7** SPICE | **Does the circuit work?** | ngspice (`src/spice/`) | every claim passes **and** every rail is covered |
| **L8** DFM | **Can this fab build it?** | rules read from a real `.kicad_pro` (`src/dfm/`) | zero errors at the profile's own severities |
| **L3'** placement | **Is it the board that was asked for?** | rule grammar (`src/placement/`) | zero violations |
| **L9** artifacts | Can anyone else open it? | Gerber, drill, BOM, KiCad 9 project, GLB (`src/fab.ts`, `src/exports.ts`) | build succeeds |

The last three are the ones added most recently, and each closed a real defect the
others could not see. L6 answers *"does the copper survive this current"*; L7 answers
*"is this current what the circuit intends"*; L8 answers *"will the fab accept it"*;
L3' answers *"is this the board that was asked for"*. A board can pass all of one and
fail another — the rover did exactly that.

---

## The three gates worth understanding

### L7 — circuit simulation
Every other stage checks the board is *well-formed*. L7 is the only one that checks it
*functions*: a regulator wired backwards routes and fabricates perfectly.

- The deck is **built here, not by a generic converter** — `circuit-json-to-spice` alone
  emits 24 lines for the rover with no ICs and no voltage source, which simulates
  nothing.
- **ICs become behavioural current sinks** from the operating point and are *labelled*,
  never dropped. Coverage is a number (75% on the rover: 21 modelled, 6 stubbed, 9 not
  represented).
- **Floating nodes get `.options rshunt=1e9`.** In DC a capacitor is an open circuit, so
  the crystal node makes the matrix singular and takes the whole solve down with it.
- **Claims that cannot fail are flagged `[TAUTOLOGY]`** — a `dc_rail` claim on a net the
  deck drives with an ideal source reads its own source voltage by arithmetic. The
  report says how many claims can actually fail.
- Gate is two-directional: every claim passes, **and** every rail is covered. Otherwise
  an agent silences the stage by claiming nothing.

Claim kinds: `dc_rail`, `node_voltage`, `current` (window), `current_max` (ceiling).

### L8 — manufacturability
Fab limits are **read out of a real KiCad project**, not typed into a table:
`--fab-profile flight_controller.kicad_pro`. Net-class clearance and the global minimum
are combined by taking the stricter. Severities come from the project's own
`rule_severities` — errors gate, warnings report.

Checks: track width, via diameter, drill, **annular ring**, hole-to-hole,
copper-to-edge, silkscreen legibility.

### L3' — placement rules
The gate that did not exist and should have. The parts agent had always emitted
`"J1 at one short edge"`, `"J2 at the opposite short edge"` — as **prose**, consumed
only by the line that pasted it into a prompt. So connectors could land anywhere and
every gate passed, because the netlist, routing, physics and fab checks were all fine.

Now `placement_rules` sits beside the prose and is machine-checked:

| Rule | Checks |
|---|---|
| `at_edge(refs, edge, max_mm)` | part sits against a named **outline edge** (`north`/`south`/`east`/`west`), or any |
| `opposite_edges(a, b)` | the two are on **opposing** sides, and both really at an edge |
| `same_edge(refs, edge)` | parts share one edge |
| `on_layer(refs \| ["*"], layer)` | nothing on the wrong **copper side** (`top`/`bottom`) — only emit if the spec asks for single-sided assembly |
| `adjacent(a, b, max_mm)` | decoupling against the pin it serves |
| `in_row(refs, axis, max_mm)` | indicator LEDs actually line up |

A rule naming a part that does not exist **fails** rather than being skipped — a typo
must not silently disable a gate. "No rules" is never reported as "passed".

**Outline edges are compass points; the copper side is top/bottom.** These are different
axes and must not share words: an edge is where a part sits *in plane*, a side is which
*face* it is soldered to. Reports that said "bottom edge" were read as "on the bottom of
the board". `src/cad/contracts.ts` already used `north|south|east|west` beside
`top|bottom`, so the placement grammar adopts that rather than inventing a third
vocabulary. A part on the bottom copper side is a normal design choice — bottom-side
connectors are common — and is only a defect when the specification asks for
single-sided assembly.

Rules are also **structurally validated the moment the parts plan arrives**, not at L3'.
The grammar constrains syntax, not sense: a weak model emits well-formed nonsense —
`opposite_edges` over three parts, an `at_edge` with no edge, a `why` that reads "board
edge". Zod cannot see any of that. Catching it at emission costs milliseconds; catching
it at L3' costs a compile, a route and a solve first. Measured against a real Qwen plan:
5 rules in, 3 problems out, before anything was built. Whether a rule is the *right* rule
for the specification remains the spec reviewer's job — a tool can only check shape.

---

## Agents

| Node | Role | Sees | Decides |
|---|---|---|---|
| `parts` | parts/topology | spec | BOM, topology, calculations, **placement rules**, L7 claims |
| `design` / `revise` | designer | spec, work order, current HDL | the HDL — **as edit blocks** |
| `operating_point` | modeler | spec, netlist | rail voltages, currents, dissipation |
| `review_physics` | physicist | L6/L7 reports, heatmaps | whether the margins are real |
| `review_layout` | layout | renders **or** the geometry digest | placement, routing, assembly |
| `review_spec` | spec | spec, netlist, renders/digest | does it do what was asked |
| `chief` | chief | everything above | one merged work order, and pass/fail |

The three reviews run concurrently in one LangGraph superstep — they are meant to
disagree. The chief resolves them and is the only node that can accept a board, and even
it is overridden by any measured hard failure.

**Vision.** Reviewers were specified against rendered PNGs. Local models are text-only,
so each model declares a `vision` capability; when false, `src/layout-digest.ts`
substitutes a **measured geometry digest** computed from the same Circuit JSON — 36
placements with edge clearances, courtyard overlaps, connector *edges*, occupancy,
routing detours, silkscreen — and the prompt states which views were withheld. This is
not a downgrade: a number is diffable, cannot be misread, and a finding raised against it
can be checked.

**Designer output is a patch, not a file.** SEARCH/REPLACE blocks (`src/hdl-patch.ts`),
applied deterministically. A full rewrite cost ~4.5k tokens and truncated mid-file; a
patch costs a few hundred, and an edit matching zero or more than one place is a hard
error rather than a silent drop.

---

## Models

| Shorthand | Serves | GPU | Use when |
|---|---|---|---|
| `--model qwen` | llama.cpp, Qwen2.5-Coder-3B, `:8200` | **none** | the GPU is needed for Isaac Sim, gsplat or training |
| `--model laguna` | vLLM, Laguna S 2.1 NVFP4, `:8100` | ~117 GB | best review quality |
| `--model stub` | in-process fixture | none | exercising the graph offline |

Any LangChain provider also works (`google-genai:…`, `anthropic:…`, `openai:…`).

**They are not equivalent, and the difference is instructive.** On the same board with
the same flags, Laguna returned 3 blockers / 2 major / 4 minor and named the via defect
with its fix; Qwen returned 0 blockers / 1 major / 5 minor and never mentioned it. **Both
verdicts were REVISE** — the gates measured identically and blocked the board either way.
A weaker model degrades the advice; it cannot degrade the verdict.

**Contention.** The GB10 has one 121 GB unified pool. Laguna's NVFP4 checkpoint is 93 GB
on disk, so lowering `--gpu-memory-utilization` frees very little before it cannot load.
Running Laguna and Isaac Sim together does not fit. Use `--model qwen` while the GPU is
busy — that is what the CPU tier is for.

---

## Running it

```bash
npm install --legacy-peer-deps      # sharp cannot build on aarch64 without libvips-dev
./tools/vendor-ngspice.sh           # rootless ngspice into .tools/

# a board from a specification
npx tsx src/cli.ts --spec examples/spec-led-indicator.md --model qwen \
  --fab-profile flight_controller.kicad_pro

# analyse existing HDL against everything
npx tsx src/cli.ts --seed examples/rover-fixed.tsx --model stub \
  --operating-point examples/rover-op.json \
  --claims examples/rover-claims.json \
  --fab-profile flight_controller.kicad_pro --iterations 1
```

| Flag | |
|---|---|
| `--spec <f>` / `--prompt` / `--seed <f.tsx>` | where the design comes from |
| `--model <id>` | `qwen`, `laguna`, `stub`, or `provider:model` |
| `--base-url <url>` | override a local endpoint |
| `--operating-point <f>` | pin the analysis to numbers you trust |
| `--claims <f>` | L7 electrical claims |
| `--fab-profile <f.kicad_pro>` | L8 fab limits |
| `--iterations <n>` | review/revise rounds, default 3 |
| `--check` | verify each model answers, then exit |

### Single stages, and the viewer

```bash
npx tsx tools/spice-check.ts examples/rover.tsx examples/rover-op.json examples/rover-claims.json
npx tsx tools/dfm-check.ts runs/<dir>/iter-0/circuit.json flight_controller.kicad_pro
npx tsx tools/placement-check.ts runs/<dir>/iter-0/circuit.json rules.json
npx tsx tools/bench.ts --boards rover,rover-packed --lanes path-a --fab-profile flight_controller.kicad_pro
npx tsx tools/make-viewer.ts runs/<dir>          # one self-contained HTML page
```

---

## What a run produces

```
runs/<name>/
  spec.md  parts-plan.{json,txt}  final.tsx  summary.json
  iter-N/
    circuit.tsx  circuit.json  report.txt  lint.txt
    schematic|pcb|assembly .svg/.png
    operating-point.json
    physics.txt  physics/thermal.svg  physics/ir-drop-<net>.svg
    spice.txt  spice/op.cir  spice/op.log
    dfm.txt  placement.txt
    reviews.json  verdict.json
  handoff/  board.glb  kicad/board.kicad_{pro,sch,pcb}
  fabrication/  *.gbr  plated.drl  unplated.drl  bom.csv
  viewer.html
```

---

## The service — calling pcb-ai from anything

`cad-generation/api` serves the CAD half at `/cad/…`; this is the PCB half at `/pcb/…`.
Dependency-free `node:http`, so it starts in a second and adds no supply chain.

```bash
npx tsx src/service/server.ts --port 8300 --fab-profile flight_controller.kicad_pro
```

| Endpoint | Does |
|---|---|
| `GET /health` | liveness, active fab profile, route list |
| `POST /pcb/designs` | register HDL → compiles, returns `design_id` + `board_report` |
| `GET /pcb/designs/:id/board_report` | outline, mounting holes, heightmap, connector edges, hotspots |
| `GET /pcb/designs/:id/assumptions` | every derived value with its provenance, risky ones flagged |
| `POST /pcb/designs/:id/check_fit` | measured violations against an `enclosure_report` |
| `POST /pcb/designs/:id/replace_within` | **re-place inside a CAD envelope** — the other half of §6 |
| `POST /pcb/designs/:id/analyse` | L6/L7/L8/L3′ reports on demand |
| `POST /pcb/designs/:id/artifacts` | build KiCad project + GLB |
| `GET /pcb/designs/:id/artifact/:name` | fetch one |

**`replace_within` is the piece that was missing.** `src/cad/client.ts` drives the
negotiation and said so itself: *"pcb-ai does not implement this yet. Until it does,
negotiate() runs one round and reports non-convergence honestly rather than pretending to
converge."* One round is a handshake — the enclosure asks the board to shrink and nothing
on this side can answer.

Answering means rewriting the `<board>` outline and **rebuilding**, because placement,
routing and the report all follow from the outline. It never nudges Circuit JSON
directly: a board whose outline changed but whose routing did not is a picture of a
board, not a board.

It only shrinks — an envelope is a ceiling, not a target — and it can legitimately fail:

```
ok: false
applied: ["board width 80mm -> 70mm", "board height 62mm -> 55mm"]
reason: 70 error(s) after re-placement (32x pcb_port_not_connected_error,
        32x pcb_trace_missing_error, 3x pcb_placement_error,
        2x pcb_component_outside_board_error, 1x pcb_autorouting_error).
        First: Plated hole pcb_plated_hole_0 violates copper-to-board-edge
        clearance (measured 0.000mm, required 0.200mm)
```

That is a `200`, not a `500` — "this envelope cannot be satisfied" is a well-formed
answer the negotiator must be able to read, and §6 says a non-converged pair is reported
as such rather than papered over. A refusal that says *why*, by error class, is the
difference between the CAD side relaxing the right constraint and guessing.

---

## The §6 negotiation, both halves live

```bash
# CAD side (once)
cd cad-generation
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn numpy build123d
.venv/bin/pip install --no-deps -e ./engine -e ./api
.venv/bin/python -m uvicorn cad_api.service:app --port 8400

# PCB side drives the loop
npx tsx tools/negotiate.ts examples/rover-fixed.tsx --fab-profile flight_controller.kicad_pro
```

`build123d` **does** install on aarch64 (0.11.1 with `cadquery-ocp-novtk`), so the CAD
engine runs on the Spark. Install `engine` and `api` with `--no-deps`: the engine
declares `anthropic` and `openai`, which the service never touches.

First live run of the loop:

```
compiling the board … 36 parts
  36 risky assumption(s) feeding the enclosure
cad  http://127.0.0.1:8400 — generators: bracket, enclosure_shell, plate, tube

PCB<->CAD: CONVERGED — fit on the first attempt; no negotiation was needed
  round 1: cavity 83.0x65.0x15.9mm  violations=1 (blocking 0)
      [minor] board_not_mechanically_secured  measured=0 limit=1
```

Two things worth reading in that.

**It converged without negotiating**, because CAD sized the cavity *around* the board
rather than imposing an envelope — so `replace_within` was never called. The half that
was missing is in place and exercised directly, but the loop has not yet been driven to
a round where CAD pushes back. A board that must fit a fixed enclosure is the case that
tests it.

**The one violation is real**: the rover declares no mounting holes, so nothing secures
it inside the shell. Minor by severity, but it is a genuine mechanical gap that no PCB
gate would ever raise — which is the argument for the loop existing.

**36 risky assumptions feed the enclosure.** Component heights are `ASSUMED` from
footprint class, and the lid is designed against them. An assumed height that is wrong
produces a lid that fits in the report and not on the bench; `assumptions.json` records
each one with its provenance.

---

## Known limits

- **Only `.op` SPICE claims exist.** `ripple`, `frequency`, `edge`, `startup` need
  `.tran` and are not implemented.
- **L7 coverage is 75%** on the rover. Raising it means real vendor `.lib` files.
- **The compile fetches `modelcdn.tscircuit.com`** and times out on this box — a network
  dependency in a local-only pipeline, and it dominates run time.
- **Path B (SchGen) is not wired**, but its tooling blocker is gone: `tools/vendor-kicad.sh`
  installs **KiCad 8.0.9** on aarch64 without root, which is exactly the version SchGen
  pins. (The 9.0 PPA is amd64-only — 8 is both the newest available here and the one
  path B wants.) `tools/bench.ts` still reports the lane `BLOCKED` until the netlist
  bridge exists.
- **The second-opinion DRC disagrees with ours, substantially.** KiCad 9 reports 46
  errors on a rover that tscircuit's DRC passes clean: 23 `clearance`, 23
  `hole_clearance`. It independently confirms two things L8 already measured (54
  under-height silkscreen texts, a hole-to-hole gap), which is what makes the 46
  credible. Under principle 4 a disagreement of this size is a pipeline bug to be
  investigated, not a number to paste into a report — nobody has yet worked out which
  engine is right.
- **The designer loop has not completed a full revision end to end** against a real
  model. Its pieces are unit-tested; the round trip is not proven.
- Gerbers are produced but a human should review before money is spent.
