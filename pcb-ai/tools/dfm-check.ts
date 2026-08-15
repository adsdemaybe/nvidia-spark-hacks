#!/usr/bin/env tsx
/**
 * Check a compiled board against a fab profile (L8), on its own.
 *
 *   npx tsx tools/dfm-check.ts runs/<dir>/iter-0/circuit.json flight_controller.kicad_pro
 *   npx tsx tools/dfm-check.ts runs/<dir>/iter-0/circuit.json        # built-in default profile
 */
import fs from "node:fs/promises"
import { runDfm, describeDfm } from "../src/dfm/index.ts"
import { loadKicadProProfile, describeProfile, DEFAULT_PROFILE } from "../src/dfm/profile.ts"

const [circuitPath, profilePath] = process.argv.slice(2)
if (!circuitPath) {
  console.error("usage: dfm-check.ts <circuit.json> [profile.kicad_pro]")
  process.exit(1)
}

const profile = profilePath ? await loadKicadProProfile(profilePath) : DEFAULT_PROFILE
console.log(describeProfile(profile))
console.log()

const circuitJson = JSON.parse(await fs.readFile(circuitPath, "utf8"))
const report = runDfm(circuitJson, profile)
console.log(describeDfm(report))
console.log()
console.log(report.errors === 0 ? "L8 PASS" : `L8 FAIL — ${report.errors} error(s)`)
process.exit(report.errors === 0 ? 0 : 1)
