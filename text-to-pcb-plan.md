# Production text-to-PCB loop — architecture plan
Everything below runs locally. No cloud EDA, no hosted routers, no SaaS DRC — and as of
**2026-08-15, no hosted inference either**: the agents run on **Laguna S 2.1 NVFP4
served by vLLM on the Spark** (§8). The local-only claim now has no exception; the
Claude/`ollama:` providers survive only as configured fallbacks, not as the default path.
The PoC proved the loop shape: agents write HDL, deterministic tooling compiles /
routes / solves / renders, agents review against measurements, a chief merges, the
designer revises. What separates the PoC from production is not more agents — it is
**more and stricter deterministic systems**, and a contract about who may say "pass".
**Two generators, one ladder (decision: 2026-08-15).** The tscircuit loop above is
**path A**. Microsoft's **SchGen** (MIT, `github.com/microsoft/SchGen`) is an
already-built text→schematic pipeline that emits native KiCad instead, and it is wired
in as **path B** (§7). They are *front-ends*, not alternatives to the architecture: both
feed the same deterministic ladder, are scored by the same gates on the same golden
boards, and the winner is decided by measured pass-rate — not by preference (§7.4).
**Working directory: `pcb-ai/`** — all code, fixtures, vendored third-party trees and
scratch for this feat live there (§9). This plan is the spec; `pcb-ai/STATE.md` is the
resume state.
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
   artifact hashed. With Laguna served locally at a pinned revision and
   `temperature=0` + fixed seed, "same model version" is now a hash we control rather
   than a vendor's silent upgrade.
6. **The generator is swappable; the ladder is not.** Any front-end that can emit
   something the ladder can ingest — tscircuit Circuit JSON, or a KiCad `.kicad_sch`
   through the netlist bridge (§7.2) — is a legitimate candidate. It earns its place on
   measured gate pass-rate over the golden set, never on architectural taste. Two
   front-ends are in flight; a third would change no gate and no report format.
7. **No claim without an assertion.** Any electrical behaviour the design is *supposed*
   to have ("3.3 V ±5% under 300 mA", "~1 Hz at OUT") is emitted as a machine-checkable
   claim and simulated at L7. A stated claim with no assertion is a gate failure, not a
   comment (§3.4).
---
## 2. Target architecture
```
   ┌──────────── judgement (Laguna S 2.1 NVFP4, local via vLLM — §8) ─────────┐
   │   intake → parts/topology → designer → reviewers(3) → chief              │
   └────┬──────────────────────────────────▲─────────────▲────────────────────┘
        │ emits                            │ diffs       │ verdicts + evidence
   ┌────▼───────────────────────┐                        │
   │ PATH A  tscircuit HDL      │  .tsx ──▶ Circuit JSON ────────────┐
   │ PATH B  SchGen (§7)        │  KiCad-API py ──▶ .kicad_sch ──▶ netlist
   └────────────────────────────┘                        │      bridge (§7.2)
                                                         │           │
   ─────────────────── determinism (no LLM) ─────────────┴───────────▼────────
   L0 lint → L1 compile → L2 ERC×2 → L3 place → L4 route → L5 DRC×2 →
   L6 physics → L7 SPICE → L8 DFM → L9 artifact build → L10 regression
                └── path B enters at L2; L0/L1 have path-B equivalents (§7.2)
```
### Stage ladder (all deterministic, all gated)
| Stage | What | Tool | Gate |
|---|---|---|---|
| L0 | HDL lint | **new: `pcb-lint`** — AST rules over the .tsx (see §3) | zero errors |
| L1 | Compile | `@tscircuit/eval` → Circuit JSON | no `_error` elements |
| L2 | ERC ×2 | `@tscircuit/checks` + own rules (pins connected, rails driven, decoupling present/near, no floating inputs) **and** `kicad-cli sch erc` on the KiCad-side schematic | both clean, findings reconciled |
| L3 | Placement | `pcbPack` (proven: −33% area vs hand) + own overlap/edge/constraint checker | all constraints hold |
| L4 | Route | capacity-autorouter, Freerouting fallback (§4) | 100% nets routed |
| L5 | DRC ×2 | tscircuit DRC **and** `kicad-cli pcb drc --exit-code-violations` via `circuit-json-to-kicad` | both clean, results reconciled |
| L6 | Physics | own PCG solvers: thermal, IR drop, current density, IPC-2221 | no hard failure vs budgets |
| L7 | Circuit sim | **ngspice** — path A via `circuit-json-to-spice`; path B via `kicad-cli sch export netlist --format spice` (§3.4) | every declared claim asserted **and** passing; zero unasserted claims |
| L8 | DFM | fab-profile check: min drill, annular ring, silkscreen clearance, courtyard, paneling (JLC/PCBWay profiles as data files) | profile clean |
| L9 | Artifacts | Gerber + drill + BOM + pick-and-place + 3D render; gerber round-parse as self-check | build succeeds |
| L10 | Regression | golden-board suite re-run on every pipeline change | no metric regressions |
An agent sees the *report* of every stage; it can influence only the design source (path
A: the `.tsx`; path B: the SchGen Python). The loop re-enters at the first stage of its
path after every revision.
**Path coverage.** L0–L1 are tscircuit-specific and have path-B equivalents rather than
direct reuse (§7.2). **L2 onward is shared and identical for both paths** — that is the
whole point of the netlist bridge: placement, routing, DRC, physics, SPICE, DFM and
artifacts are the same code on the same data, so a bake-off measures the *generator* and
nothing else.
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
"Model class" is now a *capability requirement*, not a vendor tier — §8.3 maps each row
onto a concrete local endpoint and names which rows Laguna cannot serve as-is (the three
multimodal reviewers) plus the two ways out.
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
### 3.4 SPICE stage — L7, **mandatory, both paths** (~2 weeks)
> **Status: v1 BUILT AND WIRED, 2026-08-15.** `pcb-ai/src/spice/` implements the deck
> builder, the ngspice runner and the claim evaluator; it runs in `graph.ts` beside L6
> and its failures reach `agents/chief.ts`'s hard-failure override. Measured on the
> rover: **75% model coverage, `NRST = 3.3000 V`, `R2 = 1.3 mA`, L7 PASS**; with a
> planted 100 kΩ in place of R2's 1 kΩ, **L7 FAIL (`R2 = 0.0000 A`) and the chief's PASS
> was overridden to REVISE**. ngspice 42 is vendored onto the Spark without root by
> `tools/vendor-ngspice.sh`. What is *not* built: the `.tran`-based claim kinds
> (`ripple`, `frequency`, `edge`, `startup`) — the grammar below is the target, the
> implemented subset is `dc_rail`, `node_voltage`, `current`, `current_max`. See
> `pcb-ai/STATE.md`.
**It is promoted (decision: 2026-08-15) into the core**, because it is the only stage
that checks whether the circuit *functions*. Everything else — ERC, DRC, DFM — checks whether the
circuit is *well-formed*. A board can be 100% routed, DRC-clean, DFM-clean and still be a
regulator wired to oscillate. SchGen's own evaluation has exactly this hole (netlist
similarity + ERC counts, no simulation — §7.5), which makes L7 the gate that keeps path B
honest rather than merely plausible-looking.
**Engine: ngspice** (local binary; also loadable as a shared lib). It is the one simulator
that both toolchains already reach:
- **Path A** — tscircuit ships `analogsimulation` / `voltageprobe` elements and an ngspice
  engine (`@tscircuit/ngspice-spice-engine` + `circuit-json-to-spice`, both already in the
  tree). Circuit JSON → netlist → ngspice → probe waveforms.
- **Path B** — `kicad-cli sch export netlist --format spice` on the generated
  `.kicad_sch` → the same ngspice runner. KiCad's SPICE export needs simulation model
  fields on the symbols; SchGen's generator does not emit them, so the bridge (§7.2)
  attaches models from the same part→model table path A uses. **Model coverage is the
  gating risk, not the simulator** — see §3.4.1.
**The claims contract.** The parts/topology agent must emit, alongside the BOM, a
`claims.json` — a list of machine-checkable assertions in a fixed grammar the agent
composes but never invents:
| Claim | Compiles to | Example |
|---|---|---|
| `dc_rail(net, v, tol, load_mA)` | `.op` / `.dc` sweep + node-voltage assertion | `V3V3 = 3.3 V ±5% @ 300 mA` |
| `ripple(net, mv_pp, load_mA)` | `.tran` + peak-to-peak window | `V3V3 ripple < 50 mVpp` |
| `frequency(net, hz, tol)` | `.tran` + zero-cross count over settled window | `OUT ≈ 1 Hz ±10%` |
| `edge(net, t_rise_max)` | `.tran` + 10–90% measurement | `SCL rise < 300 ns` |
| `current(component, mA_max)` | `.op` branch current | `U1 draw < 120 mA` |
| `startup(net, v, t_max)` | `.tran` from 0, first-crossing time | `V3V3 reaches 3.0 V within 10 ms` |
| `no_contention(net)` | drivers-on-net check + `.op` sanity | one driver per net |
Gate semantics, both directions:
1. every claim in `claims.json` must have a passing assertion, **and**
2. every rail and every declared function must be covered by ≥1 claim. An
   uncovered rail is an L7 failure — this is what stops the agent from silencing the
   stage by simply not claiming anything.
The blinker's "1.02 Hz" stops being a comment in the HDL and becomes a checked fact; the
regulator's "3.3 V" stops being a part-number assumption and becomes a measured node.
Failed assertions are reported to the designer agent as `(claim, expected, measured,
probe waveform)` — the most actionable feedback in the entire ladder.
#### 3.4.1 Model coverage — the honest limit
ngspice can only simulate what has a model. Reality on a mixed digital board:
- **Passives, discretes, regulators, op-amps, references, oscillators** — models exist
  (vendor `.lib`/`.mod`, or built-in primitives). These are the rails and analog claims,
  i.e. most of what actually kills a board.
- **MCUs, motor-driver ICs, USB PHYs, connectors** — no usable SPICE model. These are
  stubbed as **behavioural loads** declared in the BOM (`i_typ`, `i_max`, `v_range`,
  pin `input|output|power|passive`), auto-generated as current sinks / voltage sources.
  A stubbed part is *labelled as stubbed in the L7 report* (`MEASURED` vs `ASSUMED`
  provenance, matching the CAD plan's convention). Coverage % is a reported metric, and
  a regression in it fails L10 the same way a DRC regression does.
`vendor-models/` is a data directory beside `fab-profiles/`: one `.lib` per part number,
fetched once, hashed, checked in. A part with neither a model nor a behavioural stub is
an L7 **error**, not a silent skip.
#### 3.4.2 Two things building it taught, which the spec had wrong
Both were found by *planting a defect and checking the gate caught it* — neither showed
up in a passing run, which is the argument for negative tests in one sentence.
1. **A claim on an ideally-driven net cannot fail.** The deck drives every declared rail
   with a `V` source, so `dc_rail V3V3 = 3.3 ±5%` measures 3.3000 V by arithmetic. It
   proves the deck is wired, not that the design regulates — and a stage that counts
   tautologies as evidence inflates its own coverage. Results are now flagged
   `[TAUTOLOGY]` and the report states how many claims *can* fail; if none can, it says
   so in capitals. **The rail-under-load question is L6's**, which has the real copper;
   netlist-level SPICE has no trace resistance in it at all.
2. **A one-sided claim passes an open circuit.** `current` was specified as "stays under
   a limit", so a resistor wrong by two decades — LED current **0.0000 A**, a dark
   board — passed the gate. `current` is now a two-sided window and `current_max` is a
   separate kind for genuine ceilings. Generalised: *a bound is only a gate in the
   direction it bounds*, and most electrical claims are wrong in both directions.
#### 3.4.3 What L7 is not
Not a replacement for L6. The PCG solvers (thermal, IR drop, current density, IPC-2221)
answer *"does the copper survive this current"*; ngspice answers *"is this current what
the circuit intends"*. They consume the same operating point and must agree on it —
disagreement between the modeler agent's operating point, L6's assumed currents, and
L7's simulated currents is a **pipeline bug** filed under principle 4, exactly like a
DRC disagreement.
### 3.5 Freerouting as routing fallback (~1 week)
Proven locally in the PoC (v2.3.0 JAR, Java 25, 0 violations after the DSN pad-dedupe
fix). Wire as: capacity-autorouter first; on unrouted nets or abort, export via
`dsn-converter` (with the pad-dedupe patch upstreamed or applied in-process), route
with the canonical headless flags, import the SES. GPL-3.0 → subprocess only, JAR
fetched by a setup script, never bundled. Two known converter bugs to fix first:
duplicate pads on shared-geometry footprints; inner-layer loss on SES import.
### 3.6 DFM profiles — L8 (**BUILT AND WIRED, 2026-08-15**)
Fab constraints as data: min trace/space, min drill, annular ring, hole-to-hole,
copper-to-edge, silkscreen legibility. Checked against Circuit JSON geometry at L8. The
PoC's DRC used tscircuit's defaults; production checks against the fab that will
actually drill the board.
**The profiles do not have to be hand-written, and should not be.** A real KiCad project
already carries exactly this in `design_settings.rules`, entered by whoever set the
board up for a real fab house. `src/dfm/profile.ts` reads a `.kicad_pro` directly, so
"check this design against the same rules as that project" is one flag:
`--fab-profile flight_controller.kicad_pro`. Numbers from a board someone ordered beat
numbers someone typed. Net-class clearance and the global minimum are combined by taking
the **stricter** of the two, so a profile cannot be loosened by leaving one unset.
**What it found immediately, and this is the point of the stage:** every board in the
repo fails the reference flight-controller profile the same way — **75 vias at 0.300 mm
pad on a 0.200 mm drill, a 0.050 mm annular ring against a 0.100 mm minimum.** The
pipeline's default via stack is not manufacturable on a real 4-layer process. Nothing
had checked, so nothing had said so. Also caught: a 0.149 mm hole-to-hole gap
(0.250 mm minimum) and 54 silkscreen strings below the 0.800 mm legible minimum.
Severities come from the profile's own `rule_severities`, so a fab that treats
hole-to-hole as a warning gets a warning here — errors gate, warnings report.
### 3.7 Golden-board regression (~ongoing)
The PoC's blinker and rover become fixtures. Every pipeline change re-runs them plus
~10 more seeded specs; metrics (area, copper, vias, peak °C, IR drop, stage timings)
tracked per commit; any regression fails CI. This is what makes touching the router
or the packer safe.
---
## 4. Tool additions — the ask
> **Correction (2026-08-15, measured).** The "installed / verified" column below was
> originally recorded on the **dev laptop**, not on the Spark. Checked on the Spark
> (`gn100-dd0e`, GB10, aarch64, Ubuntu 24.04 noble) the picture is different, and the
> differences are hour-0 blockers rather than footnotes. Both columns are kept: what we
> proved somewhere, and what is true on the box that has to run the demo.
**Want, all local:**
| Tool | Role | Dev laptop | **Spark (aarch64), verified 2026-08-15** |
|---|---|---|---|
| `kicad-cli` | second DRC engine, path-B ERC + netlist + SPICE export, 3D render | KiCad 10.0.5 installed, headless verified | **NOT installed.** `apt` candidate is **7.0.11** (noble/universe arm64) — too old for `sch export netlist --format spice` reliability and older than SchGen's pin. **Action: install KiCad 8.x or 9+/10 on aarch64 (PPA or Flatpak) at H+0** — §7.6 |
| `circuit-json-to-kicad` | path-A bridge to KiCad | npm, 0.0.173 | npm, arch-independent ✔ |
| `@tscircuit/checks` | ERC base | npm, 0.0.162 | npm ✔ |
| **`ngspice` binary** | **L7 engine, both paths** | in node_modules (JS engine) | **NOT installed**; `apt` candidate **42** for arm64 → `sudo apt install ngspice` is a one-liner. Do it at H+0 |
| `circuit-json-to-spice` | path-A netlist emit | npm | npm ✔ |
| Freerouting 2.3.0 JAR | routing fallback (also SchGen's PCB step) | proven, Java 25 | **Java is 1.8.0_492** — Freerouting 2.x will not start. Needs a JDK ≥ 21/25 (`apt install openjdk-21-jdk` or Temurin aarch64) |
| ESLint 10 + typescript-eslint 8 | `eslint-plugin-pcb` engine (L0) | installed, wired | node **v24.18.1** present ✔; **`pnpm` NOT installed** (corepack it) |
| `better-sqlite3` + `SqliteSaver` | checkpoints, stage cache, metrics | npm | native build on aarch64 — verify at H+0 |
| **vLLM + `poolside/Laguna-S-2.1-NVFP4`** | **the agents themselves (§8)** | n/a | `vllm` **NOT on PATH**; `setup/serve_laguna.sh` expects it in `$STRUCT_HOME/.venv`. **`uv` NOT installed.** Shared preflight owns this |
| **SchGen** (MIT) | **path-B generator (§7)** | n/a | vendored into `pcb-ai/vendor/schgen/`; needs Python 3.10 + KiCad 8 + patches (§7.6) |
| `vendor-models/*.lib` | L7 SPICE models per part number | — | data dir, checked in, hashed (§3.4.1) |
**Adopted after evaluation:**
- **Microsoft SchGen** — MIT-licensed, KiCad-native, ships its own ERC + netlist-similarity
  evaluation and a LoRA-trained 20B generator. Wired as **path B** rather than rejected:
  it is a genuinely different attack on the same problem (spatial schematic synthesis vs.
  HDL compilation) and cheap to bolt onto a ladder we already own. Full analysis, patch
  list and kill criteria in §7.
**Considered and rejected:**
- **JITX** — auth-locked to a hosted account; violates local-only.
- **NeurPCB** — no license, requires KiCad GUI API server live; `pcbPack` already won.
- **Docker for Freerouting** — subprocess + JAR is simpler and equally isolated for
  a single-user local pipeline.
- **A hosted DRC/DFM API (PCBWay, JLC)** — local profiles as data instead.
- **An FEA package for thermal** — own PCG solver is validated against the boards
  built so far and stays; upgrade path (3D stack, transient) only when a real board
  disagrees with it.
- **SchGen's OpenRouter/Azure providers** — the code paths stay, the network calls do
  not; every provider is repointed at the local vLLM endpoint (§8.2).
- **Retraining/LoRA-tuning SchGen's GPT-oss-20b** — out of scope for 40h and it would
  compete with Isaac/gsplat for the same GB10 memory. We use the published checkpoint
  as-is, or drive SchGen's *scaffolding* with Laguna (§7.3) — the second is preferred.
`pcb-ai/spark/preflight.sh` is the single gate for all of the above: every external
binary (`kicad-cli version`, `java -version`, `ngspice -v`, `python3.10`, vLLM `/v1/models`
round-trip) fails in one second with the install command, not mid-run.
## 5. Sequencing
0. **M0 — Spark preflight** (§4, `pcb-ai/spark/preflight.sh`). ngspice, a modern
   `kicad-cli`, a JDK ≥ 21, `uv`/`pnpm`, and a vLLM/Laguna JSON-schema round-trip. Four
   of these are *missing on the Spark today* — this milestone is now the gate on
   everything else and is measured in hours, not days.
1. **M1 — gates + lint** (§3.1 ✅ shipped, §3.3 ERC, ladder skeleton, SQLite checkpoints)
   **+ Laguna wired as the agent runtime** (§8). The loop gets strict, and it gets local.
   Highest defect-catch-rate per line of code.
2. **M2 — second opinions and electrical truth** (§3.2 KiCad DRC, §3.6 DFM profiles,
   gerber round-parse, **§3.4 SPICE — promoted out of M3**, operating-point as a checked
   artifact). The loop gets trustworthy *and* stops taking the circuit's function on
   faith. Promotion rationale: L7 is the only gate that distinguishes a working board
   from a well-formed one, and path B has no equivalent of its own (§7.6).
3. **M2.5 — path B online + bake-off** (§7). Vendor SchGen, apply the patch list (§7.7),
   build the netlist bridge (§7.2), run both generators over the golden set, publish the
   scorecard (§7.4). Time-boxed: if the bridge is not producing L2-clean netlists by the
   end of the box, path B is parked with the scorecard as the record of why.
4. **M4 — routing depth** (§3.5 Freerouting fallback, layer-balance metrics,
   placement search from the earlier proposal if metrics justify it). Note Freerouting
   is also SchGen's own routing step, so this milestone serves both paths.
5. **M5 — hardening** (§3.7 regression suite incl. SPICE-coverage and per-path metrics,
   stage cache, human gate, docs).
M1+M2 ≈ a month of focused work and deliver most of the production value; M2.5–M5 are
each independently shippable. **40h hackathon cut:** M0 → M1 → the L2/L5 slice of M2 →
demo. M2.5 runs only if path A is green and someone is free; a half-integrated path B is
worse than none, because it burns the bake-off's credibility.
## 6. What stays from the PoC unchanged
- tscircuit as the HDL and Circuit JSON as the single interchange format
- LangGraph + `initChatModel` + Zod (provider-agnostic held; Gemini/Anthropic/OpenAI/
  Ollama all resolve through one path)
- the PCG physics solvers and their heatmap renders
- `pcbPack` for placement
- the measurement/judgement split — production only enforces it harder
---
## 7. Path B — Microsoft SchGen as a second generator
**Decision (2026-08-15): adopt as a parallel front-end, time-boxed, behind the same
gates.** Two independent implementations is already principle 4 for *checks*; SchGen
extends it to *generation*. When two unrelated generators produce boards that pass the
same ladder, the ladder is doing its job. When one consistently fails a stage the other
clears, we have learned something about the stage as well as the generator.
### 7.1 What SchGen actually is (read from source, 2026-08-15)
MIT-licensed, from Microsoft Research. Weights (`microsoft/SchGen`) and dataset
(`microsoft/SchGen_dataset`) on HuggingFace; training data derived from SparkFun open
hardware under CC BY-SA 4.0.
- **Representation.** Not an HDL. SchGen generates **executable Python** that drives the
  KiCad schematic API through a small verb set — `add_schematic_symbol`,
  `get_pin_location`, `add_label`, `connect_pins`, `write_out_all_wires` — placing symbols
  by coordinate on a 210×297 mm A4 sheet with origin bottom-left (`config.py:prepare_context`).
  Output is a real, human-editable `.kicad_sch`. Three abstraction levels (L1/L2/L3,
  inferred from `*_L1.kicad_sch`-style filename suffixes) trade spatial detail for brevity.
- **Model.** GPT-oss-20b with LoRA adapters on selected expert layers, trained on
  schematic→code pairs built by an agentic sketch pipeline with human alignment
  (`dataset_construction/`, `training/train.py`).
- **Its own checks.** `modules/sch_evaluation.py` runs (a) **netlist comparison** against a
  reference design — Jaccard and F1 over net sets (`netlist_comparison_new.compare_netlists_sets`)
  — and (b) **KiCad ERC**, via `kicad-cli sch erc`, counted by error class
  (`evaluation/eval.py` reports `mean_netlist_jaccard`, `mean_netlist_f1`, `mean_erc_errors`).
- **Its own PCB step.** `modules/utils/kicad_sch_to_pcb.py` shells `kicad-cli pcb update`
  to push the schematic into a `.kicad_pcb`, and `config.py` carries FreeRouting plugin
  paths for all three OSes. So a layout path exists, but it is thin — no placement
  optimiser, no DFM, no physics, no DRC reconciliation.
- **Vendored deps.** Ships its own `kiutils` and a `my_skip_lib` (skip fork) for
  s-expression schematic manipulation, so it does not depend on KiCad's Python API for
  parsing — only for library/symbol resolution and CLI export.
**Why it is worth the integration cost:** it attacks a different half of the problem.
tscircuit compiles a *netlist* and derives a schematic view; SchGen synthesises a
*drawn schematic sheet* with deliberate spatial placement — the artifact a human engineer
opens, reads, and edits. For a project whose output is meant to be handed to people, that
is not cosmetic. It also carries topology priors learned from real SparkFun boards, which
is a different failure surface from Laguna writing tscircuit from docs-RAG.
### 7.2 How it joins the ladder — the netlist bridge
Path B produces a schematic, not a board, so it cannot enter at L0/L1. **It enters at L2**,
and the bridge is deliberately at the *netlist* level:
```
SchGen py ──exec──▶ .kicad_sch ──┬─▶ kicad-cli sch erc          ──▶ L2 (KiCad side)
                                 ├─▶ kicad-cli sch export netlist ──▶ netlist IR ──┐
                                 └─▶ kicad-cli sch export netlist --format spice ─┐│
                                                                                  ▼▼
   netlist IR + footprint map ──▶ synthesised Circuit JSON (components + nets, no layout)
                              ──▶ L3 pcbPack ─▶ L4 route ─▶ L5 DRC×2 ─▶ L6 ─▶ L7 ─▶ L8 ─▶ L9
```
**Why netlist-level and not KiCad-native all the way:** routing path B through
`pcb update` + Freerouting + `kicad-cli pcb drc` would give it a *different* placer,
router, physics and DFM from path A — the bake-off would then measure four differences
at once and conclude nothing. Synthesising Circuit JSON from the netlist means **L3
onward is byte-identical code on both paths**, so the scorecard isolates the generator.
**The hard part is the footprint map, not the netlist.** KiCad symbols carry footprint
fields naming KiCad libraries; Circuit JSON needs tscircuit footprints. `pcb-ai/bridge/footprint-map.json`
is a checked-in, hashed table (`Device:R` + `Resistor_SMD:R_0402_1005Metric` → `0402`, …).
An unmapped footprint is a **bridge error**, never a guess — a silently substituted
package is exactly the class of defect this whole plan exists to prevent.
**Path-B equivalents of L0/L1:** L0 becomes static analysis of the generated Python
before execution (`santize_code.py` hardened into a real allowlist — see §7.6);
L1 becomes "the Python executes without exception and the resulting `.kicad_sch` parses",
which is the same cheap-failure-first principle applied to a different source language.
### 7.3 Which model drives path B
Three options, in preference order:
1. **Laguna drives SchGen's scaffolding (preferred).** SchGen's verb set + system prompt
   are just a code-generation contract; Laguna is a coding model served locally with
   structured output (§8). We keep SchGen's *representation, KiCad plumbing, evaluation
   and symbol-library context* and swap the generator. No 20B checkpoint to host, no
   competition for GB10 memory with Isaac/gsplat, and one model to reason about.
2. **The published SchGen checkpoint via vLLM**, as a second served model — only if
   Laguna's schematic-code quality is measurably worse on the scorecard. Costs a second
   model resident in unified memory during build hours; schedule against §8.6.
3. **LoRA-tuning anything.** Out of scope (§4, rejected).
### 7.4 The bake-off scorecard
Same specs, same golden set, same ladder. Reported per path, per board:
| Metric | Why it matters |
|---|---|
| L2 ERC-clean on first generation (%) | raw generator quality before any loop |
| Iterations to first L5-clean board | the real cost of the loop |
| Boards reaching L9 within budget (%) | end-to-end yield |
| L7 claim pass-rate + SPICE model coverage (%) | does it work, and how much did we verify |
| Final area / copper / via count | quality of the resulting board |
| Wall-clock and tokens per board | cost |
| Bridge errors (unmapped footprints, unparseable netlists) | path-B-specific tax |
| Human-editability of the schematic sheet | **recorded, not gated** — judgement, so it never decides promotion |
The scorecard is a file (`pcb-ai/runs/bench/scorecard.{json,md}`), produced by
`tools/bench.ts` and diffed per commit like every other metric.
#### 7.4.1 First run, 2026-08-15 — measured, fab profile `flight_controller`
```
| board        | lane   | gate    | parts | area mm² | copper mm | vias | peak °C | L7 pass      | L7 cov | L8 e/w | netlist J | wall s |
| rover        | path-a | FAIL    | 36    | 4960     | 1132      | 75   | 92.0    | 4/4 (2 real) | 75%    | 3/5    | —         | 50.2   |
| rover-packed | path-a | FAIL    | 36    | 3312     | 793       | 60   | 89.3    | 4/4 (2 real) | 75%    | 3/5    | 1.000     | 82.3   |
| blinker      | path-a | FAIL    | 8     | 720      | 118       | 13   | 37.9    | 0/0 (0 real) | 88%    | 3/5    | —         | 10.0   |
| blinker-1hz  | path-a | FAIL    | 9     | 816      | 124       | 13   | 41.4    | 0/0 (0 real) | 89%    | 3/4    | 0.556     | 12.3   |
| (all)        | path-b | BLOCKED | — needs KiCad 8; apt offers 7.0.11 and root is unavailable (§7.7 patch 4)           |
```
Three findings, and the second is the one that justifies the whole exercise:
1. **`pcbPack` is validated, quantitatively.** `rover-packed` versus `rover`: area
   4960 → 3312 mm² (**−33%**, matching the number the plan had been asserting), copper
   1132 → 793 mm, vias 75 → 60, peak 92.0 → 89.3 °C — **at netlist Jaccard 1.000**. The
   packer moved everything and changed no connection. That is exactly the claim a
   placement tool has to prove and could not prove before this metric existed.
2. **Every board on the board fails L8 identically**: 75 vias at 0.300 mm pad on a
   0.200 mm drill — a **0.050 mm annular ring against a 0.100 mm minimum**. The
   pipeline's *default via stack is not manufacturable* under a real 4-layer profile,
   and it never showed up because until now nothing checked against a real fab's rules.
   One systemic defect across every board is worth more than any per-board finding.
3. **`blinker-1hz` scores 0.556 against `blinker`** — correctly, they are different
   circuits (9 parts vs 8). The metric behaves.
### 7.5 Taking the best of both — what actually gets combined
The bake-off cannot be run yet, but the question "what is each good at" does not need a
race to answer; it needs the source read, which it has been. Three things are worth
taking from SchGen, and one of them is already in.
| From SchGen | Why | Status |
|---|---|---|
| **Netlist-set similarity as a metric** (Jaccard + F1 over net connection-sets) | This ladder is all *property* checks — lints, routes, holds 3.3 V, meets annular ring. **Not one of them asks "is this the circuit we asked for."** Two entirely different boards can both be DRC-clean. SchGen's `compare_netlists_sets` is the cheap structural answer | **ADOPTED — `src/bench/netlist-similarity.ts`.** Compared structurally by pin-endpoint sets, not by net name, so autogenerated `N$12` vs `N$7` matches when it should. Found the `pcbPack` result above on its first run |
| **Deliberate spatial schematic placement** | tscircuit derives a schematic view from a netlist; SchGen *composes* a readable sheet on an A4 grid with explicit coordinates. For a project whose output is handed to humans, the drawn sheet is a deliverable, not a by-product | **Not adopted yet.** The right shape is a schematic-placement pass over Circuit JSON, not a second generator |
| **A KiCad-native artifact** | SchGen's output is a file an engineer opens and edits | **ADOPTED, and by a better route — `src/exports.ts`.** `circuit-json-to-kicad` emits a full KiCad 9 project (36 footprints, 178 pads, 960 segments, 75 vias on the rover) directly from path A, with no SchGen dependency at all |
And one thing to take **from this pipeline into any SchGen lane**, which is the honest
direction of the trade: **L7.** SchGen scores netlist similarity and counts ERC errors
and stops there. A schematic can be netlist-similar to a reference, ERC-clean, and still
not work. Whatever generates the design, the simulation gate is the same code.
**The combined pipeline is therefore not a merge of two systems.** It is this ladder,
with SchGen's metric folded in as a regression scorer and a KiCad project emitted at L9
— which is where two of its three advantages already are, obtained without the KiCad-8
dependency that blocks the third. Path B remains worth running for *generator diversity*
(§7.1), and the harness has a lane waiting for it; it is no longer a prerequisite for
getting the value.
### 7.6 What SchGen does not do — and what we must add
| Gap | Consequence | Our fill |
|---|---|---|
| **No simulation of any kind** | a netlist-similar, ERC-clean schematic can still not function | **L7 ngspice is mandatory for path B too** (§3.4) — this is the single biggest thing we add to SchGen |
| No layout beyond `pcb update` | no placement quality, no DFM, no physics | L3–L8 via the bridge |
| Netlist metric needs a **reference design** | Jaccard/F1 is meaningless for a novel board with no ground truth | keep it *only* for the golden set where a reference exists; it is a regression metric, not a gate for new designs |
| `sch_verifier.visual_verify` returns an **LLM's −1/0/1 score** on a rendered image | an agent asserting correctness — direct violation of principle 1 | **demoted to advisory**: its output may be fed to the designer agent as a hint, and may never gate promotion. Keep the code, strip its authority |
| ERC counts are parsed from **text** (`count_erc_errors` splits lines) | brittle across KiCad versions | switch to `kicad-cli sch erc --format json`, parse structurally, reconcile with `@tscircuit/checks` per principle 4 |
| No provenance on generated values | resistor values arrive unattributed | claims/BOM entries from path B carry `ASSUMED` until L7 measures them |
### 7.7 Integration patch list (concrete, from source inspection)
Applied as a patch series in `pcb-ai/vendor/schgen.patches/` against a pinned commit —
never as edits to a vendored tree, so upstream stays merge-able.
1. **`modules/utils/llm_interface.py:1009` instantiates an Azure client at import time**
   (`LOCAL_HELPER = GetLLMInterface(..., model_provider="Azure")`). Any `import` of this
   module tries `AzureCliCredential` and dies offline. **Patch: make `LOCAL_HELPER` lazy**
   and default the provider from env. This is a hard blocker, not a nicety.
2. **Add a `Laguna`/`VLLM` provider** — trivial, because `OpenAILLMInterface` already uses
   `openai.OpenAI()`, which honours `OPENAI_BASE_URL`/`OPENAI_API_KEY`. In practice this is
   env config plus a `GetLLMInterface` branch so the provider name is explicit in logs (§8.2).
3. **`config.py` requires `user_name` + `openrouter_api_key`** and hardcodes per-OS paths.
   Patch to read everything from env with Linux/aarch64 defaults; no key required.
4. **KiCad version.** SchGen targets **8.0.9**; the Spark has *no* KiCad and `apt` offers
   **7.0.11**. Install a modern KiCad on aarch64 at H+0 and pin the version in preflight.
   If only 7.x is obtainable, path B is parked — say so early rather than debugging
   s-expression drift for a day.
5. **`requirements.txt` pins `flash_attn==2.8.3`** (and CUDA-specific torch). Needed only
   for the local-inference/training path, which option 7.3.1 removes. **Install a reduced
   requirement set**; do not attempt a `flash_attn` build on aarch64/sm_121 for a code path
   we do not use.
6. **`santize_code.py` [sic] guards `exec` of model-written Python.** We execute generated
   code on the Spark: replace with an explicit allowlist (imports, the five KiCad verbs,
   no `os`/`subprocess`/`open` outside the project dir) and run it in a subprocess with a
   timeout. Treat it as untrusted input, because it is.
7. **ERC/netlist export to JSON** where the CLI supports it (patch 7.5 row 5).
8. **Determinism**: seed + `temperature=0` through `GetLLMInterface`, and strip timestamps
   from generated `.kicad_sch` before hashing, so principle 5 holds on path B too.
### 7.8 Risks and kill criteria
| Risk | Sev | Mitigation / trigger |
|---|---|---|
| No usable KiCad ≥ 8 on aarch64 | **high** | H+0 test; if it fails, park path B (patch 4) |
| Footprint map coverage too thin for real boards | high | start from the golden set's parts; unmapped = hard error; measured as a scorecard column |
| `exec` of model-written Python on the Spark | med | patch 6 sandbox; non-negotiable before first run |
| Two resident models exhaust unified memory | med | prefer 7.3.1 (Laguna-driven); otherwise schedule per §8.6 |
| Path B half-integrated at demo time | med | **kill criterion:** if the bridge is not producing L2-clean netlists for ≥3 of the golden boards by the end of the M2.5 time box, park path B and ship the scorecard as the finding |
| Upstream SchGen churn | low | pinned commit + patch series |
---
## 8. Laguna as the local agent runtime
The master plan names Laguna S 2.1 NVFP4 as the project's model; this section is the
PCB-side contract for it. **Everything the agents do runs on the Spark.**
> **Status, 2026-08-15: WORKING, against the real model.** vLLM is serving
> `poolside/Laguna-S-2.1-NVFP4` on the Spark (port **8100**, served name
> `laguna-nvfp4`, 16k context, ~97 GB of the GB10's memory, ~35 min to load under
> `--enforce-eager`). `--model laguna:laguna-nvfp4 --base-url http://localhost:8100/v1`
> passes preflight and has driven a complete rover design run: **4 blockers, 1 major,
> 6 minor**, every one of them quantitative. Quality notes in §8.7.
> Two things were proven before the model existed and still hold: the transport
> (`tools/mock-openai-server.ts`, the offline stub served over HTTP) and the guarantee
> that a text-only model is never sent an image — **0 image blocks across 5 requests**,
> asserted from the server's own request log.
### 8.1 Endpoint
`setup/serve_laguna.sh` already serves it: vLLM, OpenAI-compatible, `:8000`,
`--served-model-name laguna`, `--enable-auto-tool-choice --tool-call-parser hermes`,
`--gpu-memory-utilization ${LAGUNA_GPU_FRAC:-0.60}`, `--max-model-len 131072`.
The PCB loop treats it as a plain OpenAI-compatible endpoint and holds no vLLM-specific
code, so a container swap changes nothing above the transport.
```
LLM_BASE_URL=http://spark:8000/v1   LLM_MODEL=laguna   LLM_API_KEY=local
```
### 8.2 Repointing both paths at it
- **Path A (TypeScript).** `initChatModel` already abstracts the provider; Laguna resolves
  through the OpenAI-compatible provider with `baseURL` + `apiKey: "local"`. No new
  dependency — the provider-agnostic hold from §6 pays off here.
- **Path B (Python).** `openai.OpenAI()` in `OpenAILLMInterface` honours `OPENAI_BASE_URL`,
  so path B is *env config plus one explicit provider branch* (§7.6 patch 2).
- **Fallbacks, in order:** Claude (escalation tier, network) → `ollama:` (local, weaker).
  Provider is **per-agent config, never code**, so a single agent can escalate without
  touching the graph.
### 8.3 Per-agent assignment — and the multimodal problem
| Agent | Needs | Runs on |
|---|---|---|
| intake | structured extraction | **Laguna** |
| parts/topology | reasoning + docs-RAG + `claims.json` emit | **Laguna** (escalate to Claude on repeated L7 failure) |
| designer | code generation from stage reports | **Laguna** — its core competence |
| chief | triage over structured reports | **Laguna** |
| physicist | reads **heatmaps** | see below |
| layout | reads **board renders** | see below |
| spec | reads **renders** vs spec | see below |
**The gap, stated plainly: Laguna S 2.1 is a coding model, and the three reviewers were
specified as multimodal.** Three ways out, in preference order:
1. **Make the images unnecessary (preferred, and better engineering).** Every fact those
   reviewers are supposed to *see* already exists numerically upstream: hotspot
   coordinates and peak °C from the PCG thermal solver, courtyard overlaps and edge
   clearances from the placement checker, connector positions/orientations from Circuit
   JSON geometry. Emit a **structured render digest** (JSON) and let text-only agents
   review that. This also removes a real failure mode — an agent hallucinating from a
   blurry render — and makes the reviewers' inputs diffable and cacheable.
2. **A small local VLM** (e.g. a 7–8B vision model on the same vLLM) purely for
   "does this look like a board a human would accept". Costs memory; gated by §8.6.
3. **Claude for the three reviewers only**, when network is available. Honest, but it
   breaks the local-only claim for part of the loop — so it is a fallback, and the demo
   must say so if used.
Option 1 is the default and should be built first regardless, because a structured digest
is a better reviewer input than a PNG under this plan's own principles.
**Built (2026-08-15): `pcb-ai/src/layout-digest.ts`.** Every model carries a declared
`vision` capability; `contentOf()` substitutes the digest when it is false and states in
the prompt which views were withheld, so a text-only reviewer cannot describe a picture
it never received. On the rover the digest is 36 placements with edge clearances,
courtyard-overlap pairs, connector access, occupancy, per-layer routing length, detour
ratios and silkscreen coverage — and it immediately surfaced two things worth reviewing
that no one had raised: **`SW1` sits 16.5 mm inside the board** (a push button nobody can
reach) and **`J3` is 0.42 mm from the edge**. That is the argument for option 1 in one
data point: measurement found what a picture was supposed to.
### 8.4 Structured output and determinism
- All agent I/O is Zod (TS) / Pydantic (Py) schemas; vLLM's guided decoding enforces the
  JSON schema server-side. Parse failure = bounded retry, then escalate provider.
- `temperature=0`, fixed seed, pinned model revision hash recorded in every run's
  provenance record. This is what makes principle 5's "same model versions" checkable.
- **Preflight is a schema round-trip and a tool call**, not a ping: an agent loop that
  cannot reliably emit valid JSON is a dead loop, and we want that failure at second 3.
### 8.5 Grounding
pgvector on the shared Postgres (master plan §3). PCB corpus: tscircuit docs and
`@tscircuit/props` typedefs, `@tscircuit/checks` rule list, KiCad `kicad-cli` reference,
SchGen's own verb set and symbol-library index (`kicad_scan_lib.py` output), the ngspice
manual's `.op/.tran/.dc` sections, our fab profiles and lint rule docs. Retrieval is
per-agent and logged — a wrong revision caused by a bad retrieval must be traceable.
### 8.6 Model-agnostic, demonstrated rather than asserted
The provider layer was always described as model-agnostic. It is now **tested** with
three different backends reaching the same `ChatLike` interface through the same code
path, on the same board, with the same flags:
| Backend | How it is served | Result |
|---|---|---|
| `stub` | in-process fixture | full graph, every node, no network |
| `local:qwen` | **Qwen2.5-Coder-3B-Instruct Q4_K_M** on `llama-server` (llama.cpp b10436, aarch64 CPU, port 8200) | preflight passes; complete design run |
| `laguna:laguna-nvfp4` | **vLLM serving `poolside/Laguna-S-2.1-NVFP4`** on the GB10, port 8100 | preflight passes; complete design run |
Both local backends were installed **without root**: llama.cpp is a 13 MB release
tarball, ngspice is `apt-get download` + `dpkg -x`. That matters beyond convenience —
it is what makes the "everything local" claim survive contact with a machine the agent
does not own.
The backup path is therefore real and exercised, not theoretical. If Laguna is down,
mid-load (~35 min from cold under `--enforce-eager`), or its 97 GB is needed for
training, `--model local:qwen --base-url http://127.0.0.1:8200/v1` runs the same
pipeline on CPU while the GPU does something else.
### 8.7 What the models actually produced — controlled, identical inputs
Same board, same seed HDL, same operating point, same claims, same
`--fab-profile flight_controller.kicad_pro`, same L6/L7/L8 reports, **text digest only**
(§8.3). The only variable is the model.
| | Laguna S 2.1 NVFP4 | Qwen2.5-Coder-3B (CPU) |
|---|---|---|
| Work order | **3 blocker, 2 major, 4 minor** | 0 blocker, 1 major, 5 minor |
| Raised the via / annular-ring defect? | **yes** | **no — never mentions it** |
| Verdict | REVISE | REVISE |
**Laguna** returned **3 blockers, 2 major, 4 minor**, and the findings are the kind a
human reviewer writes. It read the L8 report and converted it into a work order with
numbers: *"75 vias violate manufacturability rules: pad diameter 0.300 mm (min
0.600 mm), drill 0.200 mm (min 0.300 mm), annular ring 0.050 mm (min 0.100 mm) … use at
minimum 0.600 mm pad / 0.300 mm drill, or 0.700 mm pad / 0.300 mm drill for margin."*
It recognised the three via findings as **one defect with one fix** rather than three
independent tickets, cited measured values back (U1 at 81.2 °C, C3 at 66.3 °C, VBAT
32.1 mV against a 150 mV budget), and caught decoupling distance per device
(*"U4 (600 mA load, nearest VBAT cap 9.3 mm)"*) — all from a table of numbers, with no
image.
**Qwen-3B**, with the identical L8 report in its prompt, **never mentioned the vias at
all** — the one hard, quantified, unambiguous defect on the board. Its major finding is
technically backwards (*"increase the RDS(on) of the MOSFET"* — raising on-resistance
raises dissipation), and its summary asserts *"peak temperatures of components exceed
their ratings"* when L6 reported zero hard failures and 92 °C against a 125 °C limit. An
earlier run produced *"the peak temperature of 92.0 °C is above the allowed 125 °C"* in
one sentence. Numeric reasoning is where a 3B model gives out.
**The conclusion is not "Laguna good, Qwen bad" — it is about where correctness lives.**
Three things happened at once, and the third is the one that matters:
1. The ladder produced **identical measurements** for both. L8 found the same 3 errors
   and 5 warnings regardless of which model was reading them.
2. The **verdict was the same** — both said REVISE, so the board was blocked either way.
3. But the *reason* differed. Laguna blocked it **for the right reason**, with the fix
   attached. Qwen blocked it for partly-wrong reasons and missed the real one.
Had Qwen concluded `pass: true`, the chief's hard-failure override would have forced
REVISE anyway — proven separately by the stub run, where a model that said "accepted"
was overridden by 3 L8 errors. So the floor holds no matter how weak the model gets:
**a weaker model degrades the advice and cannot degrade the verdict.** That is principle
1, and this is the first controlled measurement of it.
The operational reading for §8.3's roster: Laguna is the right model for the reviewing
and chief roles. Qwen is a genuine availability backup — it keeps the pipeline running
and the gates honest — not a quality substitute.
### 8.8 The context budget is a design constraint, not a config value
Reviewing a seeded board exercises six of the seven agents. The seventh — **the
designer** — only runs on a revision, and that is where the first real failure appeared.
Measured on the rover revise turn, against `--max-model-len 16384`:
| Part of the turn | chars | ≈ tokens |
|---|---|---|
| HDL guide (in the system prompt) | 8,319 | 2,248 |
| current HDL | 16,888 | 4,564 |
| compiler report | 3,925 | 1,061 |
| physics analysis | 3,088 | 835 |
| work order | 3,961 | 1,071 |
| **prompt total** | **36,181** | **9,779** |
| full-file rewrite as output | 16,888 | 4,564 |
| **prompt + output, before any reasoning** | | **14,343 / 16,384** |
That leaves ~2k tokens for a **reasoning model's** thinking, and Laguna spends more than
that on an eleven-item work order. The revision came back truncated at 87 of ~296 lines.
**The failure was invisible in the worst way.** The reply had an opening ``` fence and no
closing one, `extractCode` fell back to raw text, and the run reported
`L1 [parse] Parsing error: Declaration or statement expected` on a stray `</think>`. The
linter was right and its message was useless: the file was not malformed, it was *half a
file*. A truncated revision is a **retryable transport failure**, and reporting it as a
design error sends the loop off to fix the wrong thing.
Three changes, in order of how much they matter:
1. **The designer emits a patch, not a file** (`src/hdl-patch.ts`) — SEARCH/REPLACE
   blocks applied deterministically. This is what the agent roster in §2 always
   specified ("HDL revision, **as a diff**"); the token measurement is what made it
   urgent. Output drops from ~4,564 tokens to a few hundred. The second benefit is
   larger than the first: **an edit that does not apply is caught exactly and named** —
   zero matches or more than one is a hard error quoting the offending text — whereas a
   full-file rewrite silently drops whatever the model forgot to copy, which is exactly
   the regression the work order warns against and which nothing detected.
2. **Truncation is detected and named** rather than passed to the linter, in both the
   fenced-code path and the edit-block path.
3. **The work order is capped at 4 items per pass**, blockers first, remainder deferred.
   Budget is the obvious reason; the better one is that the loop's own instructions say a
   fix that introduces a new problem is worse than the issue it closed — fewer, more
   careful changes per pass is that advice enforced instead of requested.
4. **The revise turn drops the HDL guide.** It earns its 2.2k tokens when writing a board
   from nothing; it does not when a working module in the same dialect is already in the
   prompt.
**Still recommended on the serving side:** `--max-model-len 16384` is too small for this
pipeline regardless. 40960 or 65536 gives a reasoning model room on a large board. The
mitigations above make the loop work within a tight budget; they do not make a tight
budget the right choice.
### 8.9 Contention and the night shift
Laguna at `gpu-memory-utilization 0.60` shares one GB10 with Isaac Lab, gsplat and
SmolVLA. The PCB loop is *bursty and interactive*; training is *long and batch*. Rule:
**agent hours own the endpoint, training hours own the GPU.** During training windows the
PCB loop either runs from the stage cache (most revisions are text-only and re-enter at the
first dirty stage) or flips to the Claude fallback. Never run a second resident model
(§7.3.2) during a training window.
---
## 9. `pcb-ai/` — working directory and conventions
All dev for this feat happens under `pcb-ai/`. Nothing is written to the repo root.
`pcb-ai-old/` is the pre-2026-08-15 copy, kept untouched as a reference; it is not built
against and will be deleted once the bake-off scorecard has a baseline in it.
Actual layout — **plain marks what exists and runs today, `+` marks what this plan adds**:
```
pcb-ai/
├── package.json               ✓ npm (not pnpm — single package, no workspace needed yet)
├── README.md  PLAN.md         ✓
├── STATE.md                   + resume state (house style: realsim/STATE.md)
├── eslint.config.mjs          ✓ L0 wiring
├── lint/pcb-plugin.mjs        ✓ L0 rules
├── src/
│   ├── cli.ts                 ✓ entry point, --check preflight, --model/--base-url
│   ├── graph.ts               ✓ LangGraph state machine (design→compile→solve→3 reviews→chief)
│   ├── model.ts               ✓ provider layer + laguna/local endpoints + vision capability
│   ├── layout-digest.ts       + measured geometry for text-only reviewers (§8.3 option 1)
│   ├── build.ts  fab.ts       ✓ HDL→Circuit JSON→renders; Gerber/drill/BOM
│   ├── agents/                ✓ one file per agent
│   ├── physics/               ✓ PCG solvers (thermal, IR drop, current density, IPC-2221)
│   ├── spice/                 + L7: netlist.ts (deck+coverage), run.ts (ngspice), index.ts (claims)
│   └── service/               + §10 REST + MCP surface
├── tools/
│   ├── vendor-ngspice.sh      + rootless ngspice install into .tools/ (§4)
│   ├── spice-check.ts         + run L7 alone against one HDL + operating point
│   └── mock-openai-server.ts  + the stub served over HTTP, for testing the local-model transport
├── examples/                  ✓ rover.tsx, rover-op.json, blinker*, + rover-claims.json
├── bridge/                    + path B → ladder (§7.2): netlist IR, footprint map
├── vendor/schgen{,.patches}/  + pinned upstream (MIT) + §7.7 patch series
├── fab-profiles/              + L8 data
├── vendor-models/             + per-part ngspice .lib files (L7 data, §3.4.1)
├── fixtures/                  + golden boards, expected reports, scorecard/
├── spark/preflight.sh         + §4 gate
├── .tools/                    gitignored — vendored ngspice, and anything else not ours
└── runs/                      gitignored — one directory per design run
```
Two notes on what the layout is *not*, both deliberate: there is no pnpm workspace (one
package, one `package.json` — split it when a second consumer exists, not before), and the
Python side (path B, the bridge) is not created until §7's time box actually opens.
Conventions, inherited from `realsim/` so the two feats read the same way:
- **`STATE.md` is the handoff**, updated at every stopping point: what is green, what is
  mid-fix, what the diagnosis is, what to try next in order.
- **The gate is a command, not an opinion** — `make loop` exits non-zero or the work is
  not done. Agent work orders reference a contract file and a gate command.
- **Every artifact hashed, every stage cached by input hash**, every external tool
  version recorded in the run's provenance record.
- Python side uses `uv` (as realsim does); TS side uses pnpm workspaces. Both are
  installed by `spark/preflight.sh`, which is also the H+0 blocker check.
---
## 10. The board as a service — the API for CAD and simulation
The board is not the deliverable. **The robot** is. A PCB that exists only as Gerbers is
a dead end for feats 2, 3 and 5: the enclosure has to be designed around it (F2), it has
mass and it sits somewhere on the arm (F3's Isaac/MuJoCo scenes), and the twin has to
render it (F5). So the pipeline has to publish what it built in forms those consumers can
actually load — and the negotiation loop in the master plan §6 only works if there is
something on the other end of the MCP call.
### 10.1 What already exists (audited 2026-08-15, versions from npm)
| Capability | Tool | Status |
|---|---|---|
| Interchange format | **Circuit JSON** | already the pipeline's single source of truth |
| 3D board + parts mesh | **`circuit-json-to-gltf` 0.0.119** | exists — GLB is exactly what CAD viewers, `<model-viewer>` and three.js want |
| Solid model for CAD | **`circuit-json-to-step` 0.0.42** | exists — STEP is what build123d/OpenCascade imports |
| KiCad handoff | `circuit-json-to-kicad` 0.0.173 | **WIRED — `src/exports.ts`.** Emits a full KiCad 9 project (`.kicad_pro` + `.kicad_sch` + `.kicad_pcb`): 36 footprints, 178 pads, 960 segments, 75 vias on the rover. Note the converters are staged pipelines — calling `getOutputString()` without `runUntilFinished()` writes a valid, **empty** file that opens in KiCad showing nothing |
| Run viewer | `tools/make-viewer.ts` | **BUILT.** One self-contained HTML file per run — schematic, PCB, assembly, orbitable 3D board, thermal/IR fields, every gate report, and a gate-status strip up top. three.js bundled inline by esbuild, GLB inlined as base64; no network, no dev server, ~4.3 MB |
| Gerber / drill / BOM / P&P | `circuit-json-to-gerber`, `circuit-json-to-bom-csv` | **already wired** — `src/fab.ts` emits 14 files per run |
| Per-part 3D placement | `cad_component` elements | **already in the compiled output**: `position {x,y,z}`, `rotation {x,y,z}`, `footprinter_string`, linked to `pcb_component_id` |
| Board envelope | `pcb_board` element | width, height, thickness, layer count, material |
| Component registry lookup | tscircuit MCP server (community) | exists, but it is a **registry search** server — it finds parts, it does not expose a design |
**The honest summary: the geometry exporters exist and the placement data is already in
every run's `circuit.json`. Nothing has to be invented to get a board into CAD.**
### 10.2 What does not exist — and is therefore ours to build
1. **A service.** Everything above is a library call inside a CLI. F2's CAD loop, F3's
   scene builder and F5's twin cannot `npm install` their way into a TypeScript process
   from Python. There is no endpoint, no job id, no artifact URL.
2. **A mechanical contract.** `board_report` (master plan §6) is specified but not
   emitted. Outline, mounting holes, component heightmap, connector edges, keepouts and
   thermal hotspots are all *derivable from data the pipeline already has* — the
   courtyards, the `cad_component` z-positions, the thermal field from L6 — and none of
   it is currently written out.
3. **A simulation bridge. This is the real gap.** No tool anywhere converts a PCB into
   something Isaac Sim or MuJoCo can load as a body. GLB is *visual*; a simulator needs
   mass, centre of mass, an inertia tensor and a collision shape. A 4-layer 80×62 mm FR4
   board with 36 parts is ~30 g of mass sitting at the top of a robot arm, and F3 is
   training a policy on that arm. Right now that mass is not in the sim at all.
### 10.3 The service surface
One process, two protocols over the same handlers — REST for humans and F3/F5, MCP for
the agent fleet and the §6 negotiation. It lives in `pcb-ai/src/service/` and is the same
code the CLI calls, so there is no second implementation to drift.
```
POST /design              {spec|prompt|seed, model, iterations} → {job_id}
GET  /jobs/{id}           → {state, iteration, stage, verdict}          (SSE for progress)
GET  /designs/{id}/circuit.json
GET  /designs/{id}/board_report            ← §10.4, the mechanical contract
GET  /designs/{id}/artifacts/{gerber|bom|step|glb|urdf|mjcf|usd}
POST /designs/{id}/check_fit               {enclosure_report} → {violations[]}
POST /designs/{id}/replace_within          {envelope} → {board_report}
GET  /designs/{id}/physics                 → thermal field, IR drop, hotspots
GET  /designs/{id}/spice                   → L7 claims + measured values (§3.4)
```
MCP tools map one-to-one onto those handlers: `pcb.design`, `pcb.board_report`,
`pcb.check_fit`, `pcb.replace_within`, `pcb.artifact` — which is exactly the master plan
§6 pcb-server contract, now with a place to live. Long operations return a job id; nothing
blocks a caller for the length of an autoroute.
### 10.4 `board_report` — the mechanical contract
Every field below is computed from Circuit JSON the pipeline already produces. Nothing
here needs a new solver; `src/layout-digest.ts` already measures most of it for the
text-only reviewers, which is a happy accident of building the same geometry twice.
```jsonc
{
  "outline_mm":        { "width": 80, "height": 62, "thickness": 1.4, "origin": "board-centre" },
  "mounting_holes":    [{ "x": -35, "y": 26, "diameter": 3.2, "plated": false }],
  "component_heightmap": [{ "ref": "U2", "x": 0, "y": 2, "w": 10.8, "h": 10.8, "z_max": 1.75 }],
  "connector_edges":   [{ "ref": "J1", "edge": "left", "clearance_mm": 1.96, "mate_axis": "-x" }],
  "keepouts":          [{ "reason": "antenna", "x": 30, "y": 25, "w": 8, "h": 6 }],
  "thermal_hotspots":  [{ "x": -9, "y": 20, "peak_c": 92.0, "source": "U1" }],
  "mass_properties":   { /* §10.5 */ },
  "provenance":        { "circuit_json_sha256": "…", "pipeline_run": "…", "stages_passed": ["L0","L2","L5","L6","L7"] }
}
```
`z_max` is the field CAD actually needs and the one nobody has: it comes from the
`cad_component` z-position plus the part's model height, and it is what decides whether
the enclosure lid clears the electrolytic. `thermal_hotspots` comes straight from L6's
solved field — which means **the enclosure can be designed against where the board is
actually hot**, not against a guess, and that is a genuinely new capability rather than a
plumbing exercise.
### 10.5 The simulation bridge — the part nobody has built
To put the board in Isaac Sim or MuJoCo it needs to be a **rigid body**, which means four
things, all derivable:
| Quantity | How it is computed | Provenance |
|---|---|---|
| **Board mass** | `width × height × thickness × ρ_FR4 (1850 kg/m³)` + copper mass from the actual rasterised coverage L6 already computes per layer | `MEASURED` for geometry, `CONFIRMED` for ρ |
| **Component mass** | per-part from the BOM; a package-class table (0402 → 0.6 mg, SOT-223 → 0.11 g, QFP48 → 0.4 g) where the part has no datasheet mass | `ASSUMED`, labelled |
| **Centre of mass** | mass-weighted mean of component positions (already in `cad_component`) plus the board slab | derived |
| **Inertia tensor** | thin-plate tensor for the slab + parallel-axis contribution of each part treated as a point mass at its `cad_component` position | derived, first-order |
| **Collision shape** | the board slab as a box; parts optionally as a second box from courtyard × `z_max`. A convex hull over the heightmap is the upgrade if a gripper ever has to grasp the board | derived |
Emitted as three files from one computation:
- **`board.urdf`** — a single link, `<inertial>` from the table above, `<visual>` pointing
  at the GLB, `<collision>` the box. This is the format LeRobot's SO-101 URDF already
  speaks, so the board attaches to a link with one `<joint>`.
- **`board.mjcf`** — the MuJoCo equivalent, for F3's cheap cross-check tier and F4's
  replay verification.
- **`board.usd`** — via Isaac's asset converter from the GLB + the inertial properties,
  matching F3's `asset_bundle` shape exactly (`{glb, collision/, physics.json,
  semantics.json}`) so the board is **just another asset in the scene library** rather
  than a special case.
That last point is the design decision worth making explicitly: **the PCB should enter F3
through the same `asset_bundle` contract as a scanned table.** F3's gates then apply to it
for free — USD loads headless, collision hull volume within [0.3×, 1.5×] of the visual
bbox — and nobody writes a second ingestion path.
### 10.6 What the link actually buys, beyond plumbing
- **F2 (CAD).** The §6 negotiation stops being hypothetical: `cad.design_enclosure` gets a
  real heightmap and real hotspot coordinates, and `pcb.replace_within` can re-place
  against a real envelope. Both `check_fit`s stay deterministic geometry checks.
- **F3 (RL).** The arm carries its own controller board as mass at a known offset. A
  policy trained without it is trained on the wrong dynamics — a 30 g board 60 mm off the
  wrist axis is a real moment. This is the cheapest fidelity win in the whole project.
- **F5 (twin).** The board renders in the AR overlay from the same GLB, at the same pose,
  with the thermal field available as a texture — "watch the regulator heat up while the
  arm works" is a demo shot that costs nothing extra once the bridge exists.
- **Closing the loop back to L6.** F3 knows the duty cycle the policy actually commands.
  Feeding measured motor duty back into the operating point turns L6's thermal answer
  from "at the declared 600 mA per driver" into "at the current this policy really draws".
  Not in the 40h scope; recorded because the interface above is what makes it possible.
### 10.7 Sequencing
Slots into §5 as **M2.75**, after the bake-off and before routing depth — but the first
two steps are hours, not weeks, and should land early because F2 is blocked without them:
1. **`board_report` emitter + `GET /designs/{id}/board_report`** (hours — the geometry is
   already measured in `layout-digest.ts`).
2. **`circuit-json-to-step` / `-to-gltf` wired into `fab.ts`** (hours — both packages exist).
3. **Mass properties + URDF/MJCF emitter** (~2 days — the table in §10.5 is the whole spec).
4. **The service + MCP server** (~3 days — handlers already exist as CLI code paths).
5. **USD via Isaac's converter, emitted as an `asset_bundle`** (~2 days, Spark-only).
**40h cut:** ship 1 and 2, hand F2 a real `board_report` and a STEP file, and emit the
mass properties as JSON even if the URDF wrapper does not land. A number F3 can paste into
a scene beats a file format nobody had time to test.
