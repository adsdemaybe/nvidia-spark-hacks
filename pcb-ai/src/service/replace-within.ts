/**
 * `pcb.replace_within(envelope)` — re-place the board inside a CAD-imposed envelope.
 *
 * This is the missing half of the §6 negotiation. `src/cad/client.ts` already drives the
 * loop and says so in its own comment: *"pcb-ai does not implement this yet. Until it
 * does, negotiate() runs one round and reports non-convergence honestly rather than
 * pretending to converge."* One round is a handshake, not a negotiation — the enclosure
 * asks the board to shrink and nothing on this side can answer.
 *
 * What answering actually means: the envelope constrains the outline, the HDL owns the
 * outline, and everything downstream — placement, routing, the report — follows from
 * recompiling. So this rewrites the `<board>` element and rebuilds. It does not nudge
 * geometry in the Circuit JSON, because a board whose outline changed but whose routing
 * did not is not a board; it is a picture of one.
 *
 * The honesty rule that matters here: **shrinking an outline can fail.** Parts may not
 * fit, the router may not close, courtyards may collide. When that happens this returns
 * the failure rather than a smaller board that does not work, and the negotiator reports
 * non-convergence — which is exactly what §6 says to do with a non-converged pair.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { build } from "../build.ts"
import { deriveBoardReport } from "../cad/board-report.ts"
import type { BoardReport, Envelope } from "../cad/contracts.ts"
import { runDfm } from "../dfm/index.ts"
import type { FabProfile } from "../dfm/profile.ts"
import { DEFAULT_PROFILE } from "../dfm/profile.ts"

export interface ReplaceResult {
  ok: boolean
  /** The re-placed board, when it built. */
  board_report?: BoardReport
  /** The HDL that produced it, so the caller can keep the design that fit. */
  code?: string
  /** Why it could not be done, stated in terms the negotiator can report. */
  reason?: string
  /** What changed, for the record. */
  applied: string[]
}

/** `width="80mm"` / `height="62mm"` on the board element. */
const WIDTH_RE = /(<board[^>]*?\bwidth=")([^"]+)(")/
const HEIGHT_RE = /(<board[^>]*?\bheight=")([^"]+)(")/

function mmOf(bbox: Envelope["max_outline_mm"]): { w: number; h: number } {
  return {
    w: Math.abs(bbox.max_x_mm - bbox.min_x_mm),
    h: Math.abs(bbox.max_y_mm - bbox.min_y_mm),
  }
}

/**
 * Apply an envelope to HDL source.
 *
 * Only shrinks. An envelope is a *ceiling* the enclosure can accept, so growing the
 * board to fill it would be inventing a requirement nobody stated — and would likely
 * break the fit the enclosure just computed.
 */
export function applyEnvelopeToHdl(
  code: string,
  envelope: Envelope,
): { code: string; applied: string[]; reason?: string } {
  const applied: string[] = []
  const { w, h } = mmOf(envelope.max_outline_mm)

  if (!(w > 0 && h > 0)) {
    return { code, applied, reason: `envelope outline is degenerate (${w}mm x ${h}mm)` }
  }
  if (!WIDTH_RE.test(code) || !HEIGHT_RE.test(code)) {
    return {
      code,
      applied,
      reason:
        "could not find width/height on the <board> element — this design does not " +
        "declare its outline in a form the envelope can constrain",
    }
  }

  let next = code
  const currentW = Number((code.match(WIDTH_RE)?.[2] ?? "").replace(/mm$/, ""))
  const currentH = Number((code.match(HEIGHT_RE)?.[2] ?? "").replace(/mm$/, ""))

  if (Number.isFinite(currentW) && currentW > w) {
    next = next.replace(WIDTH_RE, `$1${w}mm$3`)
    applied.push(`board width ${currentW}mm -> ${w}mm`)
  }
  if (Number.isFinite(currentH) && currentH > h) {
    next = next.replace(HEIGHT_RE, `$1${h}mm$3`)
    applied.push(`board height ${currentH}mm -> ${h}mm`)
  }

  if (!applied.length) {
    applied.push(
      `board already fits the envelope (${currentW}x${currentH}mm within ${w}x${h}mm) — no change`,
    )
  }
  return { code: next, applied }
}

/**
 * Re-place a design inside an envelope and report what came out.
 *
 * `dir` gets the rebuilt artifacts, so a caller can serve them or diff them against the
 * previous round.
 */
export async function replaceWithin(args: {
  code: string
  envelope: Envelope
  dir: string
  fabProfile?: FabProfile
}): Promise<ReplaceResult> {
  const { code, envelope, dir } = args
  const { code: next, applied, reason } = applyEnvelopeToHdl(code, envelope)
  if (reason) return { ok: false, reason, applied }

  await fs.mkdir(dir, { recursive: true })
  const result = await build(next, dir)

  // Findings first, and only then the bare compile flag.
  //
  // `build()` reports ok:false with no compileError when the source is valid but the
  // geometry is not — parts off the edge, holes inside the clearance ring, nets that
  // will not route. Checking `ok` first turned all 70 of those into "unknown compile
  // failure", which tells a negotiator nothing it can act on. The envelope was refused
  // for a specific, measurable reason and it should say which.
  const errors = result.findings.filter((f) => f.severity === "error")
  if (errors.length) {
    const kinds = new Map<string, number>()
    for (const e of errors) kinds.set(e.type, (kinds.get(e.type) ?? 0) + 1)
    const summary = [...kinds.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([t, n]) => `${n}x ${t}`)
      .join(", ")
    return {
      ok: false,
      applied,
      reason:
        `the board does not fit inside this envelope: ${errors.length} error(s) after ` +
        `re-placement (${summary}). First: ${errors[0].message}`,
    }
  }

  if (!result.ok) {
    return {
      ok: false,
      applied,
      reason:
        `the board does not compile inside this envelope: ` +
        `${result.compileError ?? "no compiler message, and no error findings either"}`,
    }
  }

  // The fab still has to be able to build the shrunken version; a tighter outline pushes
  // copper towards the edge, which is exactly what min_copper_edge_clearance guards.
  const dfm = runDfm(result.circuitJson, args.fabProfile ?? DEFAULT_PROFILE)
  if (dfm.errors > 0) {
    return {
      ok: false,
      applied,
      reason:
        `re-placement fits geometrically but breaks manufacturability: ` +
        `${dfm.violations.filter((v) => v.severity === "error")[0]?.message ?? "DFM error"}`,
    }
  }

  await fs.writeFile(path.join(dir, "circuit.tsx"), next, "utf8")
  const derived = deriveBoardReport(result.circuitJson)
  return { ok: true, board_report: derived.boardReport, code: next, applied }
}
