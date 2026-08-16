/**
 * End-to-end check: real Circuit JSON -> board_report -> enclosure -> fit -> integration
 * review.
 *
 *   CAD_API_URL=http://127.0.0.1:8210 npx tsx src/cad/integration-check.ts runs/<run>/iter-0/circuit.json
 *   npx tsx src/cad/integration-check.ts <circuit.json> --no-review     # skip the model pass
 *
 * Exits non-zero when the pair does not fit, so it works as a gate command in a
 * work order (§10 "every work order references a contract file + a gate command
 * that must exit 0").
 *
 * **Enforcement, not decoration.** `check_fit` stays the deterministic gate — this never
 * overturns a converged result into a failure for a cosmetic reason. What it *can* do is
 * block on the class check_fit cannot see: a `blocker`-severity finding fails the command
 * (exit 1) even though `check_fit` was satisfied, because `check_fit` compares a cutout's
 * edge, width and height and never its position, and that gap is exactly how a connector
 * cutout once landed outside the solid while both sides reported success. Two systems
 * agreeing about something neither of them checks is the failure this exists to catch;
 * "the fit gate passed" is not evidence against that, it is the precondition for it.
 *
 * Reviewed on **Claude** (`anthropic:claude-opus-5` by default), not the local model
 * that designed the board — Principle 4 at the model level, since a model checking its
 * own output shares its own blind spots. Nemotron was the first choice and is still a
 * valid `--model=` override, but it never held the role: this call happens once per
 * negotiation round, not inside the thousands-of-calls design loop, so the local-first
 * cost argument that keeps the CAD *design* loop (`agent_loop.py`) off any hosted API
 * does not carry over to a single review call the way it does there.
 *
 * Needs `ANTHROPIC_API_KEY` in the environment. Without one this fails loudly at the
 * `resolveModel` call below and the run is reported (not silently skipped) as
 * "integration review unavailable" — see the catch block.
 */

import { readFileSync } from "node:fs"

import { deriveBoardReport, describeAssumptions, riskyAssumptions } from "./board-report.ts"
import { CadClient, describeNegotiation, negotiate } from "./client.ts"
import { describeIntegrationReview, reviewIntegration } from "../agents/integration.ts"
import { resolveModel } from "../model.ts"

async function main(): Promise<number> {
  const args = process.argv.slice(2)
  const path = args.find((a) => !a.startsWith("--"))
  const noReview = args.includes("--no-review")
  const modelId = args.find((a) => a.startsWith("--model="))?.slice("--model=".length) ?? "anthropic:claude-opus-5"
  if (!path) {
    console.error("usage: tsx src/cad/integration-check.ts <circuit.json> [--no-review] [--model=<id>]")
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

  let blockers = 0
  if (!noReview && result.converged && result.final) {
    try {
      const model = await resolveModel({ id: modelId })
      const review = await reviewIntegration({
        model,
        board: result.final.boardReport,
        enclosure: result.final.enclosureReport,
        fit: result.rounds[result.rounds.length - 1].fit,
      })
      console.log(`\n${describeIntegrationReview(review)}`)
      blockers = review.findings.filter((f) => f.severity === "blocker").length
      if (blockers > 0) {
        console.log(
          `\nBLOCKED by integration review: ${blockers} blocker-severity finding(s) above. ` +
            `The deterministic fit check passed and this stands regardless — check_fit ` +
            `does not look at connector position, component-vs-cavity clash detail, or ` +
            `standoff-to-hole alignment the way this review does.`,
        )
      }
    } catch (err) {
      // Best-effort by design, same as the docs RAG: a reviewer being unreachable must
      // not turn a converged, gate-passing board into a failure. It is reported, not
      // swallowed, so a silently-dead reviewer does not read as "nothing to find".
      const msg = (err as Error).message.split("\n")[0]
      const hint = /api.?key/i.test(msg) && modelId.startsWith("anthropic:")
        ? " — set ANTHROPIC_API_KEY, or pass --model=nemotron / --model=coder-next for a local reviewer"
        : ""
      console.log(`\n(integration review unavailable: ${msg}${hint})`)
    }
  }

  return result.converged && blockers === 0 ? 0 : 1
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(err)
    process.exit(1)
  },
)
