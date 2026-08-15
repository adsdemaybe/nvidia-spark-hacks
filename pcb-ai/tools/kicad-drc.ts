#!/usr/bin/env tsx
/**
 * Run KiCad's DRC on a board, on its own — the principle-4 second opinion.
 *
 *   npx tsx tools/kicad-drc.ts runs/<dir>/iter-0/circuit.json
 *   npx tsx tools/kicad-drc.ts board.kicad_pcb
 *
 * Given Circuit JSON it exports a KiCad project first, so there is one command between
 * "a board exists" and "a second engine has checked it".
 */
import fs from "node:fs/promises"
import path from "node:path"
import { exportKicad } from "../src/exports.ts"
import { runKicadDrc, describeKicadDrc, kicadDrcBlockers } from "../src/kicad/drc.ts"

const input = process.argv[2]
if (!input) {
  console.error("usage: kicad-drc.ts <circuit.json | board.kicad_pcb>")
  process.exit(1)
}

let pcb = input
const dir = path.join("runs", "kicad-drc")

if (input.endsWith(".json")) {
  const circuitJson = JSON.parse(await fs.readFile(input, "utf8"))
  process.stdout.write("exporting KiCad project … ")
  const results = await exportKicad(circuitJson, dir, "board")
  const board = results.find((r) => r.name === "kicad_pcb")
  if (!board?.file) {
    console.log("failed")
    console.error(board?.error ?? "no .kicad_pcb produced")
    process.exit(1)
  }
  console.log(`${((board.bytes ?? 0) / 1024).toFixed(0)} kB`)
  pcb = board.file
}

const report = await runKicadDrc({ kicadPcb: pcb, dir })
console.log()
console.log(describeKicadDrc(report))

const blockers = kicadDrcBlockers(report)
if (blockers.length) {
  console.log()
  for (const b of blockers) console.log(`  ${b}`)
}
console.log()
console.log(report.errors === 0 ? "KICAD DRC PASS" : `KICAD DRC FAIL — ${report.errors} error(s)`)
process.exit(report.errors === 0 ? 0 : 1)
