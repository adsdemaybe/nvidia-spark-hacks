# Production text-to-PCB loop — architecture plan
Everything below runs locally. No cloud EDA, no hosted routers, no SaaS DRC. Model
inference is the single exception, and the `ollama:` provider closes even that when
required.
The PoC proved the loop shape: agents write HDL, deterministic tooling compiles /
routes / solves / renders, agents review against measurements, a chief merges, the
designer revises. What separates the PoC from production is not more agents — it is
**more and stricter deterministic systems**, and a contract about who may say "pass".
---
## 1. Governing principles
1. **Determinism owns correctness; agents own intent.** An agent never asserts a
   clearance, a temperature, or a connection. Tools measure; agents interpret and
   revise. Any check a tool can perform is removed from agent judgement entirely.
2. **Every gate is a hard gate.** The PoC's chief could be argued with; production
   gates cannot. A gate failure blocks promotion to the next stage no matter what any
   agent concludes.
3. **Fail at the earliest, cheapest stage.** Lint the HDL before compiling (ms),
   compile before routing (s), route before solving (tens of s), solve before
   review (LLM cost). A defect caught at stage N must not be discoverable at N−1.
4. **Two independent implementations for every load-bearing check.** The PoC's DRC is
   tscircuit's own; production cross-checks with KiCad's engine. Agreement is signal;
   disagreement is a pipeline bug worth more than either result.
5. **Reproducible by construction.** Same spec + same seed + same model versions →
   byte-identical Circuit JSON. All state on disk, every stage resumable, every
   artifact hashed.
---
## 2. Target architecture
```
        ┌────────────────────────────────────────── judgement (LLM) ───┐
        │  intake → parts/topology → HDL designer → reviewers → chief  │
        └───────┬──────────────────────────▲───────────▲───────────────┘
                │ emits                    │ diffs     │ verdicts + evidence
   ─────────────▼────────────── determinism (no LLM) ──┴───────────────────
   L0 lint → L1 compile → L2 ERC → L3 place → L4 route → L5 DRC×2 →
   L6 physics → L7 SPICE → L8 DFM → L9 artifact build → L10 regression
```
### Stage ladder (all deterministic, all gated)
| Stage | What | Tool | Gate |
|---|---|---|---|
| L0 | HDL lint | **new: `pcb-lint`** — AST rules over the .tsx (see §3) | zero errors |
| L1 | Compile | `@tscircuit/eval` → Circuit JSON | no `_error` elements |
| L2 | ERC | `@tscircuit/checks` + own rules (pins connected, rails driven, decoupling present/near, no floating inputs) | zero errors |
| L3 | Placement | `pcbPack` (proven: −33% area vs hand) + own overlap/edge/constraint checker | all constraints hold |
| L4 | Route | capacity-autorouter, Freerouting fallback (§4) | 100% nets routed |
| L5 | DRC ×2 | tscircuit DRC **and** `kicad-cli pcb drc --exit-code-violations` via `circuit-json-to-kicad` | both clean, results reconciled |
| L6 | Physics | own PCG solvers: thermal, IR drop, current density, IPC-2221 | no hard failure vs budgets |
| L7 | Circuit sim | ngspice via tscircuit's `analogsimulation` elements + `circuit-json-to-spice` | waveform assertions pass |
| L8 | DFM | fab-profile check: min drill, annular ring, silkscreen clearance, courtyard, paneling (JLC/PCBWay profiles as data files) | profile clean |
| L9 | Artifacts | Gerber + drill + BOM + pick-and-place + 3D render; gerber round-parse as self-check | build succeeds |
| L10 | Regression | golden-board suite re-run on every pipeline change | no metric regressions |
An agent sees the *report* of every stage; it can influence only the HDL. The loop
re-enters at L0 after every revision.
### Agent roster (fewer, sharper than the PoC)
| Agent | In | Out | Model class |
|---|---|---|---|
| intake | free-text ask | structured spec: functions, rails, envelope, connectors, budgets | strong |
| parts/topology | spec | BOM + topology + calculations + layout constraints (PoC's parts agent, kept) | strong |
| designer | spec + BOM + stage reports | HDL revision, **as a diff** | strong |
| physicist | physics reports + heatmaps | electrical/thermal findings the gates rate as soft | multimodal, mid |
| layout | renders + placement data | placement/routing sanity, ergonomics, connector orientation | multimodal, mid |
| spec | renders + spec | does the board do what was asked | multimodal, mid |
| chief | everything | merged work order, priority-ordered | mid |
The three-way review fan-out **stays** (decision: 2026-08-14). The overlap between
physics/layout/spec findings is redundancy in the useful direction — independent eyes
on the same board — and LangGraph runs them in one superstep so the fan-out costs no
wall-clock. The modeler agent survives only to produce the operating point, and its
output becomes a **checked artifact** (currents must sum, rails must match the BOM)
rather than trusted prose.
### Orchestration
LangGraph stays. Additions over the PoC:
- **Checkpoint to SQLite** (`SqliteSaver`) instead of memory — resumable across
  process restarts, inspectable with plain SQL.
- **Stage cache**: each stage keyed by hash(inputs); unchanged HDL re-enters at the first
  dirty stage rather than recompiling the world. The 65s full-evaluation cost of the
  PoC drops to near-zero for text-only revisions.
- **Budgets as state**: iteration count, wall-clock, and token spend are graph state;
  the chief sees remaining budget and must triage rather than demand everything.
- **Human gate node**: optional interrupt before L9 artifact emission — production
  boards get a human eye before Gerbers are declared final, by policy not by hope.
---
## 3. The deterministic systems to build (the real work)
Ranked by return on effort. §3.1–3.3 are the production core; the PoC has none of them.
### 3.1 `eslint-plugin-pcb` — HDL linter (**built, v0.1 shipped 2026-08-14**)
No PCB-aware linter existed anywhere (npm and web searched), so the engine choice was
the decision: **ESLint 10 + typescript-eslint 8** — mature JSX/TS parser, autofix
machinery, editor integration for free — with the electronics rules as a local plugin
(`lint/pcb-plugin.mjs`, config in `eslint.config.mjs`, pipeline gate in `src/lint.ts`).
Wired as L0: lint errors skip the compile and route down the existing failure path.
Verified: 8/8 planted defects caught on a negative-test board; clean boards pass; found
one real latent bug in the rover (crystal without maxTraceLength) on first run.
v0.1 rules — each one a failure that burned a real iteration in the PoC:
- `known-elements` — unknown JSX element (compiler drops them silently)
- `trace-selectors` — selector names a missing component, pin, or net (the `.SWCLK`
  typo class that cascaded to 60 errors)
- `decoupling-length` — power-to-ground cap without explicit
  `maxDecouplingTraceLength` (topology-aware: crystal load caps exempt)
- `crystal-length` — crystal without explicit `maxTraceLength` (silent 10mm rule)
- `no-pcb-rotation` — rotates pads but not the courtyard the placement DRC checks
- `unit-strings` — bare numbers where unit strings are required
- `unique-names` — duplicate reference designators
- `chip-pin-attributes` (warn) — chips without power/ground declarations
v0.2 backlog: prop-name validation against @tscircuit/props typedefs, part-outside-
envelope, pinLabels-vs-footprint pin-count arity, autofixes. Lint messages are written
to be actionable by a model — a 50s compile round-trip becomes a 50ms lint round-trip.
### 3.2 KiCad as the second DRC engine (~1 week)
`kicad-cli` 10.0.5 is already on this machine and headless
(`kicad-cli pcb drc --exit-code-violations --format json`). Bridge:
`circuit-json-to-kicad` (0.0.173) → `.kicad_pcb` → DRC → parse JSON report → reconcile
with tscircuit's own findings. Disagreements file as pipeline bugs. This is the
principle-4 cross-check, and it is nearly free because the board format converter
already exists.
KiCad also brings `pcb render` (ray-traced 3D) — a better reviewer input than the
PoC's SVG for connector orientation and mechanical sanity.
### 3.3 ERC layer (~1 week)
`@tscircuit/checks` (0.0.162) as the base, plus own rules the PoC ran inside the
physics step, promoted to their own stage so they gate before routing: every IC power
pin reaches a driven rail, every rail has a source, no two drivers on one net without
a declared bus, reset/enable pins terminated, decoupling per power pin present.
### 3.4 SPICE stage (~2 weeks)
tscircuit ships `analogsimulation` / `voltageprobe` elements and an ngspice engine
(`@tscircuit/ngspice-spice-engine`, plus `circuit-json-to-spice`, both already in the
tree). Production shape: the parts agent emits **testable claims** ("~1Hz at OUT",
"3.3V ±5% at V3V3 under 300mA") → compiled into probe elements + waveform assertions →
ngspice runs → assertions gate. The blinker's "1.02 Hz" stops being a comment and
becomes a checked fact.
### 3.5 Freerouting as routing fallback (~1 week)
Proven locally in the PoC (v2.3.0 JAR, Java 25, 0 violations after the DSN pad-dedupe
fix). Wire as: capacity-autorouter first; on unrouted nets or abort, export via
`dsn-converter` (with the pad-dedupe patch upstreamed or applied in-process), route
with the canonical headless flags, import the SES. GPL-3.0 → subprocess only, JAR
fetched by a setup script, never bundled. Two known converter bugs to fix first:
duplicate pads on shared-geometry footprints; inner-layer loss on SES import.
### 3.6 DFM profiles (~1 week)
Fab constraints as data (`fab-profiles/jlcpcb-2layer.json`, …): min trace/space,
min drill, annular ring, mask sliver, silkscreen-over-pad, board size limits.
Checked against Circuit JSON geometry at L8. The PoC's DRC used tscircuit's defaults;
production checks against the fab that will actually drill the board.
### 3.7 Golden-board regression (~ongoing)
The PoC's blinker and rover become fixtures. Every pipeline change re-runs them plus
~10 more seeded specs; metrics (area, copper, vias, peak °C, IR drop, stage timings)
tracked per commit; any regression fails CI. This is what makes touching the router
or the packer safe.
---
## 4. Tool additions — the ask
**Want, all local, all verified available:**
| Tool | Role | Status on this machine |
|---|---|---|
| `kicad-cli` (KiCad 10) | second DRC engine, 3D render, gerber cross-check | **installed**, headless verified |
| `circuit-json-to-kicad` | bridge to it | npm, 0.0.173 |
| `@tscircuit/checks` | ERC base | npm, 0.0.162 |
| ngspice engine + `circuit-json-to-spice` | L7 simulation | already in node_modules |
| Freerouting 2.3.0 JAR | routing fallback | proven locally; needs Java 25 (installed) |
| ESLint 10 + typescript-eslint 8 | `eslint-plugin-pcb` engine | **installed, wired as L0** |
| `better-sqlite3` + `SqliteSaver` | checkpoints, stage cache, metrics | npm |
**Considered and rejected:**
- **JITX** — auth-locked to a hosted account; violates local-only.
- **NeurPCB** — no license, requires KiCad GUI API server live; `pcbPack` already won.
- **Docker for Freerouting** — subprocess + JAR is simpler and equally isolated for
  a single-user local pipeline.
- **A hosted DRC/DFM API (PCBWay, JLC)** — local profiles as data instead.
- **An FEA package for thermal** — own PCG solver is validated against the boards
  built so far and stays; upgrade path (3D stack, transient) only when a real board
  disagrees with it.
Preflights for every external binary (`kicad-cli version`, `java -version` ≥ 25,
ngspice load) join the existing `--check`, so a missing tool fails in one second with
the install command, not mid-run.
## 5. Sequencing
1. **M1 — gates + lint** (§3.1 ✅ shipped, §3.3, ladder skeleton, SQLite checkpoints).
   The loop gets strict. Highest defect-catch-rate per line of code.
2. **M2 — second opinions** (§3.2 KiCad DRC, §3.6 DFM profiles, gerber round-parse).
   The loop gets trustworthy.
3. **M3 — electrical truth** (§3.4 SPICE, operating-point as checked artifact).
   The loop stops taking the circuit's function on faith. **Confirmed in scope
   (decision: 2026-08-14).**
4. **M4 — routing depth** (§3.5 Freerouting fallback, layer-balance metrics,
   placement search from the earlier proposal if metrics justify it).
5. **M5 — hardening** (§3.7 regression suite, stage cache, human gate, docs).
M1+M2 ≈ a month of focused work and deliver most of the production value; M3–M5 are
each independently shippable.
## 6. What stays from the PoC unchanged
- tscircuit as the HDL and Circuit JSON as the single interchange format
- LangGraph + `initChatModel` + Zod (provider-agnostic held; Gemini/Anthropic/OpenAI/
  Ollama all resolve through one path)
- the PCG physics solvers and their heatmap renders
- `pcbPack` for placement
- the measurement/judgement split — production only enforces it harder
