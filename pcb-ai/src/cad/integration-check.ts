/**
 * End-to-end check: real Circuit JSON -> board_report -> enclosure -> fit.
 *
 *   CAD_API_URL=http://127.0.0.1:8210 npx tsx src/cad/integration-check.ts runs/<run>/iter-0/circuit.json
 *
 * Exits non-zero when the pair does not fit, so it works as a gate command in a
 * work order (§10 "every work order references a contract file + a gate command
 * that must exit 0").
 */

import { readFileSync } from "node:fs"

import { deriveBoardReport, describeAssumptions, riskyAssumptions } from "./board-report.ts"
import { CadClient, describeNegotiation, negotiate } from "./client.ts"

async function main(): Promise<number> {
  const path = process.argv[2]
  if (!path) {
    console.error("usage: tsx src/cad/integration-check.ts <circuit.json>")
    return 2
  }

  const raw = JSON.parse(readFileSync(path, "utf8"))
  const circuitJson: any[] = Array.isArray(raw) ? raw : (raw.circuitJson ?? raw.circuit_json ?? [])
  console.log(`circuit json: ${circuitJson.length} elements from ${path}`)

  const { boardReport, assumptions } = deriveBoardReport(circuitJson, {
    designId: path,
  })

  const bb = boardReport.outline_mm.points
  const xs = bb.map((p) => p.x_mm)
  const ys = bb.map((p) => p.y_mm)
  console.log(
    `\nboard_report:\n` +
      `  outline      ${(Math.max(...xs) - Math.min(...xs)).toFixed(2)} x ` +
      `${(Math.max(...ys) - Math.min(...ys)).toFixed(2)} mm\n` +
      `  thickness    ${boardReport.thickness_mm} mm\n` +
      `  mount holes  ${boardReport.mounting_holes.length}\n` +
      `  components   ${boardReport.component_heightmap.length}\n` +
      `  connectors   ${boardReport.connector_edges.length}` +
      (boardReport.connector_edges.length
        ? ` (${boardReport.connector_edges.map((c) => `${c.ref}:${c.edge}`).join(", ")})`
        : ""),
  )

  console.log(`\n${describeAssumptions(assumptions)}`)

  const cad = new CadClient()
  const health = await cad.health()
  console.log(`\ncad service: ok=${health.ok} generators=${health.generators.join(",")}`)

  const result = await negotiate(cad, boardReport, { emitArtifacts: true })
  console.log(`\n${describeNegotiation(result)}`)

  if (result.converged && result.final) {
    const er = result.final.enclosureReport
    console.log(
      `\nenclosure:\n` +
        `  cavity    ${er.cavity_mm.length_mm.toFixed(2)} x ${er.cavity_mm.width_mm.toFixed(2)} x ${er.cavity_mm.height_mm.toFixed(2)} mm\n` +
        `  outer     ${er.outer_mm ? `${er.outer_mm.length_mm.toFixed(2)} x ${er.outer_mm.width_mm.toFixed(2)} x ${er.outer_mm.height_mm.toFixed(2)} mm` : "n/a"}\n` +
        `  wall      ${er.wall_thickness_mm} mm   material=${er.material}\n` +
        `  mass      ${er.mass_kg !== null ? `${(er.mass_kg * 1000).toFixed(1)} g` : "n/a"}\n` +
        `  standoffs ${er.standoff_positions.length}\n` +
        `  cutouts   ${er.port_cutouts.length}\n` +
        `  step      ${er.artifacts.step_path || "(not emitted)"}\n` +
        `  glb       ${er.artifacts.glb_path || "(not emitted)"}`,
    )
  }

  const risky = riskyAssumptions(assumptions).length
  if (risky > 0) {
    console.log(
      `\nNOTE: ${risky} component height(s) were ASSUMED from a footprint table, not measured.\n` +
        `      The cavity depth above is only as good as those guesses. Supply\n` +
        `      heightOverrides from datasheets before this enclosure is manufactured.`,
    )
  }

  return result.converged ? 0 : 1
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(err)
    process.exit(1)
  },
)
