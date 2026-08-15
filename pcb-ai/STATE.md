# Resume state — 2026-08-15

`pcb-ai/` is the working directory for F1 (text-to-PCB). It was seeded from the repo's
previous `pcb-ai-old/`, which has now been **deleted** — it held no unique source, only
stale run output, and it remains in git history at `f7b67e1`. Its one genuinely better
detail (defaulting Laguna to port 8100 / `laguna-nvfp4`) was adopted before removal, and
its USB-C indicator spec was preserved as `examples/spec-usbc-indicator.md`.

Spec: `../text-to-pcb-plan.md` — that document is normative; this file is what is green
right now and what to do next.

## Verified working — measured on this machine (Spark, GB10, aarch64, Ubuntu 24.04)

### Models — three backends, one code path
| Backend | Served by | Preflight | Full design run |
|---|---|---|---|
| `stub` | in-process fixture | ✅ | ✅ |
| `local:qwen` | llama.cpp `llama-server` b10436, **CPU**, port 8200, Qwen2.5-Coder-3B Q4_K_M | ✅ | ✅ |
| `laguna` | **vLLM, `poolside/Laguna-S-2.1-NVFP4`**, port 8100, ~97 GB GPU | ✅ | ✅ |

`--model laguna` now defaults to **`http://localhost:8100/v1`** and model
**`laguna-nvfp4`** — where `serve_laguna.sh` actually puts it. (Port 8000 previously
hosted the mock fixture server, so defaulting there pointed runs at canned answers that
look like a working model.) Laguna takes **~35 min to load from cold** under
`--enforce-eager`.

### Controlled model comparison — identical inputs, only the model varies
| | Laguna S 2.1 NVFP4 | Qwen2.5-Coder-3B (CPU) |
|---|---|---|
| Work order | **3 blocker, 2 major, 4 minor** | 0 blocker, 1 major, 5 minor |
| Raised the via/annular defect? | **yes**, with the fix and margins | **no — never mentions it** |
| Verdict | REVISE | REVISE |

Both blocked the board; only Laguna blocked it for the right reason. Qwen's major
finding is backwards ("increase the RDS(on) of the MOSFET"). The gates measured the
same for both — a weaker model degrades the advice, not the verdict.

### The ladder
| Stage | State | Evidence |
|---|---|---|
| L0 lint | working | rover clean |
| L1 compile | working | 36 parts, 34 nets, 0 errors |
| L6 physics | working | peak 92.0 °C, IR 32.1 mV vs 150 mV budget |
| **L7 SPICE** | **new, working** | `NRST = 3.3000 V`, `R2 = 1.3 mA`, 75% model coverage |
| **L8 DFM** | **new, working** | 3 errors / 5 warnings vs `flight_controller.kicad_pro` |
| L9 artifacts | working + extended | 14 fab files, **plus KiCad 9 project and GLB** |

**Both gates verified in the failing direction, which is the only direction that
matters.** Planting a 100 kΩ where R2's 1 kΩ belongs → L7 FAIL (`R2 = 0.0000 A`).
Rover as-is → L8 FAIL (75 vias, 0.050 mm annular ring).

The override was proven with `--model stub`, where the model says "accepted" every time:
L7's planted defect and L8's 3 errors each forced `PASS → REVISE`
(*"(Overridden: N hard failure(s) …)"*). In the Laguna and Qwen runs the override never
had to fire — both models concluded REVISE on their own. The net is there and tested;
it just was not needed.

### Handoff and viewing
- **KiCad 9 project** — `src/exports.ts` → `.kicad_pro` + `.kicad_sch` + `.kicad_pcb`.
  Verified: 36 footprints, 178 pads, 960 segments, 75 vias. Opens in the KiCad GUI.
- **GLB** — 2.0 MB, board plus placed parts.
- **Run viewer** — `npx tsx tools/make-viewer.ts runs/<dir>` → one 4.3 MB self-contained
  HTML: schematic, PCB, assembly, orbitable 3D, thermal/IR fields, all gate reports, and
  a gate-status strip. No network, no dev server.

### Benchmark
`npx tsx tools/bench.ts --boards rover,rover-packed,blinker,blinker-1hz --lanes path-a,path-b --fab-profile flight_controller.kicad_pro`
→ `runs/bench/scorecard.{md,json}`. Headline: **`pcbPack` cuts area 4960 → 3312 mm²
(−33%), copper 1132 → 793 mm, vias 75 → 60, at netlist Jaccard 1.000** — it moved
everything and changed no connection.

## The designer role — the one part that was still failing

Reviewing a seeded board exercises six of seven agents. The **designer** only runs on a
revision, and that is where it broke. On the rover revise turn against
`--max-model-len 16384`: prompt ~9,779 tokens + full-file rewrite ~4,564 tokens =
**14,343 of 16,384 before a reasoning model thinks at all.** Laguna truncated at 87 of
~296 lines, and because the closing code fence never arrived the raw reply was written
as HDL — surfacing as `L1 [parse] Parsing error` on a stray `</think>`. The linter was
right and its message was useless: the file was not malformed, it was half a file.

Fixed, in order of impact:
1. **`src/hdl-patch.ts` — the designer emits SEARCH/REPLACE blocks, not a whole file.**
   What §2 of the plan always specified. Output drops to a few hundred tokens, and an
   edit that matches zero or >1 times is a hard error quoting the text, where a
   full-file rewrite silently drops whatever the model forgot to copy.
2. **Truncation is detected and named** in both `extractCode` and the edit-block path,
   so it reads as a retryable transport failure instead of a design error.
3. **Work order capped at 4 items per pass**, blockers first.
4. **Revise turn drops the HDL guide** (−2.2k tokens); the current module is a better
   example of the dialect than any guide.

Unit-tested against the real truncated output and real file content: exact match,
whitespace-relaxed match, multi-edit, ambiguous-match rejection, truncation detection.

**Still recommended on the serving side:** restart vLLM with `--max-model-len 40960`
(or 65536). The mitigations make the loop work inside a tight budget; they do not make a
tight budget correct.

## Bugs caught this session, all by negative tests

1. **A one-sided `current` claim passed an open circuit** (measured 0.0000 A, gate said
   PASS). `current` is now a two-sided window; `current_max` is the separate one-sided
   kind. *Plant the defect before trusting the gate.*
2. **A `dc_rail` claim on an ideally-sourced net cannot fail.** Flagged `[TAUTOLOGY]`;
   the report now counts how many claims *can* fail.
3. **`summary.json` said `hard_failures: 0`** on a board the chief had just blocked over
   three L8 violations — it counted only L6. Now counts L6 + L7 + L8.
4. **KiCad export wrote valid, empty files.** The converters are staged pipelines;
   `getOutputString()` without `runUntilFinished()` yields a header and no board.
5. **ngspice singular matrix** on the crystal node (a cap to ground, crystal skipped,
   MCU pin unmodelled). Fixed with `.options rshunt=1e9`.

## Known gaps, in the order to close them

1. **`modelcdn.tscircuit.com` is fetched during compile and times out** (~10 s × N
   parts) — a network dependency in a local-only pipeline, and it dominates run time
   (rover 50 s, most of it waiting). Cache or disable.
2. **Path B (SchGen) blocked**: needs KiCad 8.0.9; apt here offers 7.0.11 and install
   needs root. The bench harness has the lane and reports `BLOCKED` with this reason.
   Plan §7.7 patch 4.
3. **Only `.op` claims exist.** `ripple`/`frequency`/`edge`/`startup` (plan §3.4) need
   `.tran`. Verify they fail loudly as unsupported rather than silently pass.
4. **L7 model coverage 75%** on the rover. Raising it means real vendor `.lib` files in
   `vendor-models/`.
5. **§10 board-as-a-service not started** — but `board_report` is now mostly derivable
   from `layout-digest.ts` + `exports.ts`, and STEP is one package away.
6. **`npm install` needs `--legacy-peer-deps`** (`sharp` can't build on aarch64 without
   `libvips-dev`).
7. Missing on this box: `kicad-cli`, JDK ≥ 21 (java is 1.8, Freerouting needs 25),
   `uv`, `pnpm`.

## How to run it

```bash
npm install --legacy-peer-deps
./tools/vendor-ngspice.sh                    # rootless ngspice into .tools/

# offline
npx tsx src/cli.ts --seed examples/rover.tsx --model stub \
  --operating-point examples/rover-op.json --claims examples/rover-claims.json \
  --fab-profile flight_controller.kicad_pro --iterations 1

# on Laguna (vLLM) — defaults to localhost:8100 / laguna-nvfp4
npx tsx src/cli.ts --check --model laguna
npx tsx src/cli.ts --seed examples/rover.tsx --model laguna:laguna-nvfp4 \
  --base-url http://localhost:8100/v1 --operating-point examples/rover-op.json \
  --claims examples/rover-claims.json --fab-profile flight_controller.kicad_pro

# on the CPU backup
~/.local/llamacpp/llama-b10436/llama-server -m ~/.local/models/qwen2.5-coder-3b-q4_k_m.gguf \
  --host 127.0.0.1 --port 8200 -c 32768 -t 16 --jinja -a qwen &
npx tsx src/cli.ts --seed examples/rover.tsx --model local:qwen \
  --base-url http://127.0.0.1:8200/v1 --operating-point examples/rover-op.json

# single stages, and the viewer
npx tsx tools/spice-check.ts examples/rover.tsx examples/rover-op.json examples/rover-claims.json
npx tsx tools/dfm-check.ts runs/<dir>/iter-0/circuit.json flight_controller.kicad_pro
npx tsx tools/bench.ts --boards rover,rover-packed --lanes path-a --fab-profile flight_controller.kicad_pro
npx tsx tools/make-viewer.ts runs/<dir>
```
