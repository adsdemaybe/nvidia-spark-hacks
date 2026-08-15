# cad-generation API — the CAD half of the PCB↔CAD contract

Implements §6 of `master-plan.md` over the `engine/` package: a deterministic
enclosure generator and fit checker that `pcb-ai` (TypeScript) can call.

```
pcb-ai (TS)                      cad-generation (Py)
  circuit.json                     ┌──────────────────────────┐
      │  deriveBoardReport()       │  design_enclosure        │
      ▼                            │  check_fit               │
  board_report ──HTTP/JSON────────▶│  constrain_board         │
      ◀───── enclosure_report ─────│                          │
      ◀───── envelope ─────────────│  → STEP / GLB / STL      │
                                   └────────┬─────────────────┘
                                            │ enclosure_shell
                                            ▼
                                   engine.geometry.registry
                                   engine.evaluate()  ← the one harness
```

## Why the enclosure registers into the *engine's* geometry registry

`enclosure_shell` is registered with `engine.geometry.registry.register`, not a
CAD-local registry. That makes an enclosure an ordinary `Link`, so
`engine.evaluate()` scores it with the same criteria as any other part. A
CAD-only verdict path would have breached §1.1 — agents propose, *the* harness
disposes, and there is only one harness.

`GET /health` shows the effect: `enclosure_shell` appears alongside the engine's
own `bracket`, `plate`, `tube`.

## Run it

```bash
cd cad-generation/engine && python3 -m venv .venv && .venv/bin/pip install -e .
cd ../api && ../engine/.venv/bin/pip install -e .
CAD_PORT=8210 ../engine/.venv/bin/python -m cad_api.service
```

`CAD_PORT` (default 8200), `CAD_HOST` (127.0.0.1), `CAD_ARTIFACT_DIR`
(/tmp/cad-artifacts).

> **Port note.** 8200 is often taken on the Spark (a `llama-server` instance
> lives there). Pick a free port explicitly; the service exits with
> `address already in use` rather than silently sharing.

## Endpoints

| HTTP | §6 tool | Notes |
|---|---|---|
| `POST /cad/design_enclosure` | `cad.design_enclosure(board_report, intent)` | returns `enclosure_report`, `fit`, the `RobotIR`, and `engine.evaluate()` output |
| `POST /cad/check_fit` | `cad.check_fit(board_report)` | deterministic geometry only |
| `POST /cad/constrain_board` | `cad.constrain_board(reason)` | returns an `envelope` in **board** coordinates |
| `GET /health` | — | registered generators, artifact dir |
| `GET /cad/materials` | — | keys from `engine.catalogue` |
| `GET /artifacts/{name}` | — | STEP/GLB/STL, path-confined |

Calls are **synchronous**. The hub contract is "long ops return a job id" (§3),
but enclosure generation runs in well under a second; a queue is ceremony until
a tier-2/3 simulation lands behind `design_enclosure`.

## Fit checks

All deterministic, all reporting `measured`/`limit` rather than a bare boolean so
the negotiator can tell a 0.2 mm interference from a 40 mm one (§6, §8).

| code | severity | fires when |
|---|---|---|
| `board_exceeds_cavity` | blocker | board X or Y extent > cavity |
| `stack_exceeds_cavity_height` | blocker | standoff + board + tallest part > cavity height |
| `connector_without_cutout` | blocker | connector wants a cutout, none exists |
| `cutout_wrong_edge` | blocker | cutout is on a different wall than the connector |
| `cutout_too_small` | major | cutout does not fully cover the connector opening |
| `mounting_hole_unsupported` | major | no standoff within 0.5 mm of a board hole |
| `standoff_in_keepout` | major | standoff boss intrudes into a declared keepout |
| `mounting_hole_diameter_mismatch` | minor | pilot vs board hole differ by > 0.2 mm |
| `board_not_mechanically_secured` | minor | no mounting holes at all — board rattles |
| `sealed_enclosure_thermal_risk` | minor | > 5 W dissipated with no openings |

`ok` is false only for `blocker`/`major`. The two thermal/secured checks are
deliberately **minor and explicitly labelled as flags, not verdicts**: this API
runs no thermal simulation, and snap-fit designs legitimately have no holes.
`FitResult` enforces the invariant that `ok` cannot be true while a blocking
violation exists.

## The component-height problem — read this before manufacturing

**Circuit JSON has no Z axis.** `pcb_component.width`/`.height` are the
footprint's X and Y extents on the board plane; nothing in the format records how
tall a part stands. But cavity depth is exactly that number.

`deriveBoardReport` therefore fills heights from a footprint table in
`pcb-ai/src/cad/board-report.ts`, and returns **every one of them as an
`ASSUMED` assumption** with its source. Pass `heightOverrides` from datasheets to
get `CONFIRMED` values instead.

The failure this exists to prevent: an enclosure that passes every deterministic
gate, gets ordered, and then won't close because an electrolytic nobody measured
is 4 mm taller than the table guessed.

## From TypeScript

```ts
import { deriveBoardReport, describeAssumptions } from "./cad/board-report.ts"
import { CadClient, negotiate, describeNegotiation } from "./cad/client.ts"

const { boardReport, assumptions } = deriveBoardReport(circuitJson, {
  heightOverrides: { C1: 10.5 },   // measured -> CONFIRMED
})
console.log(describeAssumptions(assumptions))

const cad = new CadClient({ baseUrl: "http://127.0.0.1:8210" })
const result = await negotiate(cad, boardReport, { replaceWithin })
console.log(describeNegotiation(result))
```

`negotiate()` implements §6's loop including the 3-round hard stop, and reports a
non-converged pair **as non-converged** rather than returning the last attempt as
though it worked.

### What the PCB side still owes

`pcb.replace_within(envelope) -> board_report` does not exist in pcb-ai yet. Until
it does, `negotiate()` runs one round and, if the pair doesn't fit, stops and says
exactly that. Supply it via the `replaceWithin` option to close the loop.

## Gate command

```bash
CAD_API_URL=http://127.0.0.1:8210 npx tsx src/cad/integration-check.ts <circuit.json>
```

Exits 0 on convergence, 1 otherwise — usable as a work-order gate (§10).

## Tests

```bash
cd cad-generation/api && ../engine/.venv/bin/python -m pytest tests/ -q   # 26 tests
cd cad-generation/engine && .venv/bin/python -m pytest tests/ -q          # 37 tests
```

Every fit check has a negative test that constructs a pair wrong in exactly one
way and asserts that specific code fires — a checker that only ever returns
`ok=True` is indistinguishable from no checker.

## aarch64 / DGX Spark

`build123d` 0.11.1 installs and does real solid modelling on the Spark:
`cadquery_ocp_novtk` ships a `manylinux_2_31_aarch64` wheel. The plan's
high-severity CAD wheel risk (§13) does not materialise.
