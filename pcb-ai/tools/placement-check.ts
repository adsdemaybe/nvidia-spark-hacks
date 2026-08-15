#!/usr/bin/env tsx
/**
 * Check placement rules against a compiled board, on its own.
 *
 *   npx tsx tools/placement-check.ts runs/<dir>/iter-0/circuit.json rules.json
 *   npx tsx tools/placement-check.ts runs/<dir>/iter-0/circuit.json     # just report edges
 */
import fs from "node:fs/promises"
import { checkPlacement, describePlacement } from "../src/placement/check.ts"
import type { PlacementRule } from "../src/placement/constraints.ts"

const [circuitPath, rulesPath] = process.argv.slice(2)
if (!circuitPath) {
  console.error("usage: placement-check.ts <circuit.json> [rules.json]")
  process.exit(1)
}

const circuitJson = JSON.parse(await fs.readFile(circuitPath, "utf8"))
const rules: PlacementRule[] = rulesPath
  ? JSON.parse(await fs.readFile(rulesPath, "utf8"))
  : []

const report = checkPlacement(circuitJson, rules)
console.log(describePlacement(report))
console.log()
// "No rules" is not "passed". Saying PASS when nothing was checked is exactly the kind
// of false assurance the rest of this pipeline exists to avoid.
if (report.unchecked) {
  console.log("PLACEMENT UNCHECKED — no rules supplied, so nothing was verified")
  process.exit(0)
}
console.log(
  report.violations.length === 0
    ? `PLACEMENT PASS — ${report.checked} rule(s) checked`
    : `PLACEMENT FAIL — ${report.violations.length} violation(s)`,
)
process.exit(report.violations.length === 0 ? 0 : 1)
