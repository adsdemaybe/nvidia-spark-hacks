/**
 * Write a §6 `board_report` to disk for each named run.
 *
 *   npx tsx src/cad/dump-board-report.ts rover-power rover-motor-driver rover-indicator
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

import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"

import { deriveBoardReport, describeAssumptions, riskyAssumptions } from "./board-report.ts"

const OUT_DIR = resolve(
  dirname(new URL(import.meta.url).pathname),
  "../../../cad-generation/designs/board_reports",
)

function main(): number {
  const runs = process.argv.slice(2)
  if (!runs.length) {
    console.error("usage: tsx src/cad/dump-board-report.ts <run>...")
    console.error("  reads runs/<run>/iter-0/circuit.json")
    return 2
  }

  mkdirSync(OUT_DIR, { recursive: true })

  for (const run of runs) {
    const path = `runs/${run}/iter-0/circuit.json`
    const raw = JSON.parse(readFileSync(path, "utf8"))
    const circuitJson: any[] = Array.isArray(raw) ? raw : (raw.circuitJson ?? raw.circuit_json ?? [])

    const { boardReport, assumptions } = deriveBoardReport(circuitJson, { designId: run })
    const out = resolve(OUT_DIR, `${run}.board_report.json`)
    writeFileSync(out, JSON.stringify(boardReport, null, 2))

    const risky = riskyAssumptions(assumptions)
    console.log(
      `${run}: holes=${boardReport.mounting_holes.length} ` +
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
