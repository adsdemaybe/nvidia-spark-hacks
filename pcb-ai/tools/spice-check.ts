#!/usr/bin/env tsx
/**
 * Run L7 on its own, against one HDL file and one operating point.
 *
 * The SPICE stage is the slowest thing to debug from inside the full loop, because a
 * bad deck looks identical to a bad design from four nodes up. This runs the stage
 * alone, writes the deck it generated, and prints the report.
 *
 *   npx tsx tools/spice-check.ts examples/rover.tsx examples/rover-op.json examples/rover-claims.json
 */
import fs from "node:fs/promises"
import path from "node:path"
import { build } from "../src/build.ts"
import { describeSpice, runSpice, type Claim } from "../src/spice/index.ts"
import type { OperatingPoint } from "../src/schemas.ts"

const [hdlPath, opPath, claimsPath] = process.argv.slice(2)
if (!hdlPath || !opPath) {
  console.error("usage: spice-check.ts <hdl.tsx> <operating-point.json> [claims.json]")
  process.exit(1)
}

const outDir = path.join("runs", "spice-check")
await fs.mkdir(outDir, { recursive: true })

const code = await fs.readFile(hdlPath, "utf8")
const operatingPoint: OperatingPoint = JSON.parse(await fs.readFile(opPath, "utf8"))
const claims: Claim[] = claimsPath ? JSON.parse(await fs.readFile(claimsPath, "utf8")) : []

process.stdout.write("compiling … ")
const result = await build(code, outDir)
if (!result.ok) {
  console.log("FAILED")
  console.error(result.compileError ?? "unknown compile failure")
  process.exit(1)
}
console.log(`${result.components.length} parts, ${result.netlist.length} nets`)

const report = await runSpice({ build: result, operatingPoint, claims, dir: outDir })
console.log()
console.log(describeSpice(report))
console.log()
console.log(report.ok ? "L7 PASS" : `L7 FAIL — ${report.hardFailures.length} hard failure(s)`)
for (const f of report.hardFailures) console.log(`  ${f}`)
if (report.deckPath) console.log(`\ndeck: ${report.deckPath}`)
process.exit(report.ok ? 0 : 1)
