/**
 * Write a §6 `board_report` to disk for each named run.
 *
 *   npx tsx src/cad/dump-board-report.ts rover-power rover-motor-driver rover-indicator
 *   npx tsx src/cad/dump-board-report.ts hand-driver@1
 *
 * `integration-check.ts` already derives a board report, but it derives it into
 * a running CAD service and prints a summary. The CAD side's `gen_step()`
 * enclosure generators are files, not a service — they need the report as a file
 * too, and re-deriving it inside Python would put a second implementation of
 * this derivation on the other side of the contract.
 *
 * Output lands in cad-generation/designs/board_reports/<run>.board_report.json,
 * which is what `cad-generation/enclosures/board_enclosure.py` reads.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"

import { deriveBoardReport, describeAssumptions, riskyAssumptions } from "./board-report.ts"

const OUT_DIR = resolve(
  dirname(new URL(import.meta.url).pathname),
  "../../../cad-generation/designs/board_reports",
)

/**
 * The iteration whose circuit.json describes the board that would be built.
 *
 * `iter-0` was hardcoded here and that is wrong for any run whose first HDL did
 * not compile — `hand-driver`'s iter-0 has no circuit.json at all, so the dump
 * failed on the one board a hand actually needs. The last iteration that
 * produced a circuit is the design the run arrived at, so that is the default;
 * `<run>@<n>` pins a specific one when an earlier iteration is the one wanted.
 */
function circuitPath(spec: string): { run: string; path: string } {
  const [run, pinned] = spec.split("@")
  if (pinned !== undefined) return { run, path: `runs/${run}/iter-${pinned}/circuit.json` }

  const iters = readdirSync(`runs/${run}`)
    .filter((d) => /^iter-\d+$/.test(d))
    .map((d) => Number(d.slice(5)))
    .filter((n) => existsSync(`runs/${run}/iter-${n}/circuit.json`))
    .sort((a, b) => a - b)
  if (!iters.length) throw new Error(`no iteration of ${run} produced a circuit.json`)
  return { run, path: `runs/${run}/iter-${iters[iters.length - 1]}/circuit.json` }
}

function main(): number {
  const specs = process.argv.slice(2)
  if (!specs.length) {
    console.error("usage: tsx src/cad/dump-board-report.ts <run>[@<iter>]...")
    console.error("  reads the last runs/<run>/iter-N/circuit.json unless @N pins one")
    return 2
  }

  mkdirSync(OUT_DIR, { recursive: true })

  for (const spec of specs) {
    const { run, path } = circuitPath(spec)
    const raw = JSON.parse(readFileSync(path, "utf8"))
    const circuitJson: any[] = Array.isArray(raw) ? raw : (raw.circuitJson ?? raw.circuit_json ?? [])

    const { boardReport, assumptions } = deriveBoardReport(circuitJson, { designId: run })
    const out = resolve(OUT_DIR, `${run}.board_report.json`)
    writeFileSync(out, JSON.stringify(boardReport, null, 2))

    const risky = riskyAssumptions(assumptions)
    console.log(
      `${run} (${path}): holes=${boardReport.mounting_holes.length} ` +
        `components=${boardReport.component_heightmap.length} ` +
        `connectors=${boardReport.connector_edges.length} ` +
        `risky-assumptions=${risky.length} -> ${out}`,
    )
    // Component heights are not in Circuit JSON and never will be; the enclosure
    // is sized by them anyway. Printing the risky ones is the only thing
    // standing between a guessed height and a lid that does not close.
    if (risky.length) console.log(describeAssumptions(risky))
  }
  return 0
}

process.exitCode = main()
