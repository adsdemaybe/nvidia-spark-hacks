/**
 * The integration reviewer — a second pair of eyes on the PCB↔CAD seam.
 *
 * `check_fit` decides whether a board fits its enclosure, and it is deterministic, and it
 * stays the gate. This does not replace it. It exists because of what `check_fit` cannot
 * see, demonstrated on this project rather than imagined:
 *
 *   `connector_edges` stored "position along the wall" while the CAD side read the pair as
 *   literal board coordinates, so every east/west cutout was placed outside the solid. The
 *   boolean subtracted nothing and the connector stayed walled in. **check_fit compares a
 *   cutout's edge, width and height and never its position**, so both sides agreed about a
 *   cutout that did not exist, and agreed for weeks.
 *
 * Two systems agreeing about something neither of them checks is the failure this reviewer
 * is for. It runs on a different model from the designer — Principle 4 at the model level,
 * since a model reviewing its own output shares its own blind spots — and on a model that
 * can **see**, because a cutout in the wrong place is obvious in a render and invisible in
 * a table of numbers.
 *
 * **It proposes; it never disposes.** Its output is findings, which become work-order items
 * for the next round. A `converged: true` from `check_fit` stands whatever this says, and a
 * finding here cannot pass a board that the gates failed. The rule is the same one the rest
 * of the project runs on, and it matters most exactly where a model is most persuasive.
 */

import { z } from "zod"
import { askStructured, contentOf, supportsVision, type ChatLike } from "../model.ts"
import type { BoardReport, EnclosureReport, FitResult } from "../cad/contracts.ts"

export const IntegrationFindingSchema = z.object({
  severity: z.enum(["blocker", "major", "minor"]).describe(
    "blocker: the assembly cannot be built as described. major: it can be built and will " +
      "not work as intended. minor: worth fixing, not worth another round.",
  ),
  owner: z.enum(["pcb", "cad", "both"]).describe(
    "Which side changes to fix this. `pcb` moves copper or parts; `cad` moves walls, " +
      "cutouts or standoffs; `both` when the two constraints genuinely conflict.",
  ),
  description: z.string().describe("What is wrong, in one sentence, naming the part or feature."),
  suggested_fix: z.string().describe("The specific change, with a dimension where one applies."),
  evidence: z.string().describe(
    "What you are reading this from: a number in the reports, or something visible in the " +
      "render. If it is the render, say what you can see.",
  ),
})

export const IntegrationReviewSchema = z.object({
  summary: z.string().describe("One sentence: is this assembly buildable, and if not, why."),
  findings: z.array(IntegrationFindingSchema),
})

export type IntegrationReview = z.infer<typeof IntegrationReviewSchema>

const SYSTEM = `
You review the seam between a PCB and the enclosure built around it. Two independent
tools produced these: one designed the board, the other fitted a housing to it. Your job
is to find where they disagree about the same physical object.

The deterministic fit check has already run and its verdict is final — you cannot pass a
board it failed, and you cannot fail one it passed. What you add is the class of problem
it does not look for:

- a connector whose opening is on the right wall but in the wrong place along it, so the
  plug cannot reach the socket
- a component taller than the cavity it sits under, where the height came from a table of
  assumptions rather than a datasheet
- mounting holes that do not line up with standoffs, or a board held at one corner
- a cutout that exists in the report and is not in the solid
- anything the render shows that the numbers do not

Be specific and be quantitative. "The connector placement looks wrong" is not actionable;
"J1 sits 2.5mm below the cavity floor, so its opening is in the wall below the board" is.

If the two sides agree and the assembly is sound, return no findings. An empty list is a
real answer and a better one than a manufactured concern.
`.trim()

/** Everything the reviewer is given, as text. Numbers come from the reports, not from us. */
function describeAssembly(
  board: BoardReport,
  enclosure: EnclosureReport,
  fit: FitResult,
): string {
  const out: string[] = []
  out.push("<board>")
  out.push(`  design: ${board.design_id || "(unnamed)"}`)
  const o = board.outline_mm.points
  const xs = o.map((p) => p.x_mm)
  const ys = o.map((p) => p.y_mm)
  out.push(`  outline: ${(Math.max(...xs) - Math.min(...xs)).toFixed(1)}mm x ` +
    `${(Math.max(...ys) - Math.min(...ys)).toFixed(1)}mm, ${board.thickness_mm}mm thick`)
  out.push(`  mounting holes: ${board.mounting_holes.length}`)
  for (const h of board.mounting_holes) {
    out.push(`    (${h.x_mm.toFixed(1)}, ${h.y_mm.toFixed(1)}) dia ${h.diameter_mm}mm`)
  }
  out.push(`  connectors: ${board.connector_edges.length}`)
  for (const c of board.connector_edges) {
    out.push(`    ${c.ref} on the ${c.edge} edge at (${c.x_mm.toFixed(1)}, ${c.y_mm.toFixed(1)}), ` +
      `${c.width_mm.toFixed(1)}x${c.height_mm.toFixed(1)}mm, cutout=${c.needs_cutout}`)
  }
  const tall = [...board.component_heightmap].sort((a, b) => b.height_mm - a.height_mm).slice(0, 5)
  if (tall.length) {
    out.push(`  tallest components:`)
    for (const c of tall) {
      out.push(`    ${c.ref} ${c.height_mm.toFixed(2)}mm at (${c.x_mm.toFixed(1)}, ${c.y_mm.toFixed(1)}) ${c.side}`)
    }
  }
  out.push("</board>", "", "<enclosure>")
  const cav = enclosure.cavity_mm
  out.push(`  cavity: ${cav.length_mm.toFixed(1)} x ${cav.width_mm.toFixed(1)} x ${cav.height_mm.toFixed(1)}mm`)
  out.push(`  board origin in cavity frame: (${enclosure.board_origin_mm.x_mm.toFixed(1)}, ` +
    `${enclosure.board_origin_mm.y_mm.toFixed(1)})`)
  out.push(`  standoffs: ${enclosure.standoff_positions.length}`)
  out.push(`  port cutouts: ${enclosure.port_cutouts.length}`)
  for (const p of enclosure.port_cutouts) {
    out.push(`    ${p.ref} on the ${p.edge} edge at (${p.x_mm.toFixed(1)}, ${p.y_mm.toFixed(1)}), ` +
      `${p.width_mm.toFixed(1)}x${p.height_mm.toFixed(1)}mm`)
  }
  out.push("</enclosure>", "", "<fit_check>")
  out.push(`  ok: ${fit.ok}`)
  out.push(`  violations: ${fit.violations.length}`)
  for (const v of fit.violations) {
    out.push(`    [${v.severity}] ${v.code}${v.ref ? ` (${v.ref})` : ""}: ${v.detail} ` +
      `(measured ${v.measured}${v.unit} vs limit ${v.limit}${v.unit})`)
  }
  out.push("</fit_check>")
  return out.join("\n")
}

/**
 * Review one round of the PCB↔CAD negotiation.
 *
 * `images` are the assembly's rendered views (typically `assembly.png`, from the CAD
 * side, showing the board seated in its enclosure). They are passed only when the model
 * can actually use them — `contentOf` substitutes a measured-geometry digest otherwise,
 * and tells a text-only model in as many words not to claim it saw anything. Handing an
 * image to a model that cannot use it does not degrade gracefully: it answers confidently
 * about something it never received, which is worse than no image at all.
 */
export async function reviewIntegration(args: {
  model: ChatLike
  board: BoardReport
  enclosure: EnclosureReport
  fit: FitResult
  images?: Record<string, string>
}): Promise<IntegrationReview> {
  const { model, board, enclosure, fit, images = {} } = args
  const vision = supportsVision(model)

  const content = await contentOf(
    [
      "Review this board and the enclosure fitted to it.",
      describeAssembly(board, enclosure, fit),
    ],
    vision ? images : {},
    { vision },
  )

  return askStructured<IntegrationReview>(
    model,
    IntegrationReviewSchema,
    "integration_review",
    SYSTEM,
    content,
  )
}

/** Render a review for the run log and for the next round's work order. */
export function describeIntegrationReview(r: IntegrationReview): string {
  if (!r.findings.length) {
    return `  integration  no findings — ${r.summary}`
  }
  const lines = [`  integration  ${r.findings.length} finding(s) — ${r.summary}`]
  for (const f of r.findings) {
    lines.push(`            · [${f.severity}/${f.owner}] ${f.description}`)
    lines.push(`              fix: ${f.suggested_fix}`)
    lines.push(`              via: ${f.evidence}`)
  }
  return lines.join("\n")
}
