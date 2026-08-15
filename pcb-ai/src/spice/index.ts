/**
 * L7 — circuit simulation.
 *
 * Every other stage checks that the board is *well formed*: the HDL lints, the netlist
 * compiles, the rules pass, the copper survives the current, the fab can build it. None
 * of them checks that the circuit *works*. A regulator wired backwards routes and
 * fabricates exactly as well as one wired correctly.
 *
 * So this stage asks the only question the others cannot: with the declared parts and
 * the declared loads, does the board hold up the voltages it claims to?
 *
 * The contract is two-directional, and the second half is what makes it a gate rather
 * than a formality:
 *
 *   1. Every claim must be asserted and must pass.
 *   2. Every rail must be covered by at least one claim.
 *
 * Without (2) an agent silences the stage by claiming nothing — the deck simulates, no
 * assertion fails, and a board with an unchecked rail reports as verified. Coverage is
 * therefore itself a gate, and the uncovered rails are named.
 */
import path from "node:path"
import { z } from "zod"
import type { OperatingPoint } from "../schemas.ts"
import type { BuildResult } from "../types.ts"
import { buildDeck, coverageSummary, type ComponentCoverage } from "./netlist.ts"
import { locateNgspice, simulate } from "./run.ts"

/**
 * The claim grammar.
 *
 * Deliberately closed: an agent composes claims, it does not invent claim types. A new
 * kind of assertion is a change to this file and to the code that evaluates it, which
 * is the point — every claim type has a defined measurement behind it.
 */
export const ClaimSchema = z.object({
  kind: z
    .enum(["dc_rail", "node_voltage", "current", "current_max"])
    .describe(
      "dc_rail: a supply net holds its voltage under the declared load. " +
        "node_voltage: a net sits within a window. " +
        "current: a branch current sits within a window — use this when the current is " +
        "supposed to have a value, e.g. an LED that must actually light. " +
        "current_max: a branch current stays at or below a ceiling — use this for a " +
        "limit, where zero is an acceptable answer.",
    ),
  target: z
    .string()
    .describe("Net name for dc_rail/node_voltage; component reference for current claims."),
  expected: z.number().describe("Expected value — volts for voltage claims, amperes for current."),
  tolerance: z
    .number()
    .describe(
      "Allowed deviation. Fractional for dc_rail (0.05 = ±5%), absolute for node_voltage " +
        "and current claims.",
    ),
  why: z.string().describe("What breaks if this is not true. One sentence."),
})

export type Claim = z.infer<typeof ClaimSchema>

/** Claims measured as a branch current through a component, rather than a node voltage. */
function isCurrentClaim(claim: Claim): boolean {
  return claim.kind === "current" || claim.kind === "current_max"
}

export interface ClaimResult {
  claim: Claim
  pass: boolean
  measured?: number
  detail: string
  /**
   * True when the claim cannot fail by construction.
   *
   * A `dc_rail` claim on a net that the deck drives with an ideal voltage source will
   * always measure exactly that voltage — it confirms the deck is wired, not that the
   * design regulates. That is worth reporting and worth nothing as evidence, and the
   * difference has to be visible or the stage inflates its own coverage. Netlist-level
   * SPICE has no trace resistance in it; the rail-under-load question belongs to L6,
   * which solves the actual copper.
   */
  tautological?: boolean
}

export interface SpiceReport {
  available: boolean
  /** ngspice version and where it came from, so a report is reproducible. */
  engine?: string
  ok: boolean
  claims: ClaimResult[]
  coverage: ComponentCoverage[]
  coveragePercent: number
  /** Rails with no claim against them — a gate failure, not a warning. */
  uncoveredRails: string[]
  problems: string[]
  deckPath?: string
  /** Hard failures: anything that must block promotion out of this stage. */
  hardFailures: string[]
}

/** The measurable node for a claim, or null when the claim names something absent. */
function claimNode(claim: Claim, deckNodes: Map<string, string>): string | null {
  return deckNodes.get(claim.target) ?? null
}

export async function runSpice(args: {
  build: BuildResult
  operatingPoint: OperatingPoint
  claims: Claim[]
  dir: string
}): Promise<SpiceReport> {
  const { build, operatingPoint: op, claims } = args
  const dir = path.join(args.dir, "spice")

  const engine = await locateNgspice()
  const deck = buildDeck({ build, operatingPoint: op, title: "L7 operating point" })
  const cov = coverageSummary(deck.coverage)

  // Coverage gate: a rail nobody claimed anything about was not verified.
  const claimedNets = new Set(
    claims.filter((c) => !isCurrentClaim(c)).map((c) => c.target),
  )
  const uncoveredRails = op.rails.map((r) => r.net).filter((n) => !claimedNets.has(n))
  /** Nets the deck drives with an ideal source — see `ClaimResult.tautological`. */
  const drivenRails = new Set(op.rails.map((r) => r.net))

  const base: SpiceReport = {
    available: Boolean(engine),
    engine: engine ? `ngspice-${engine.version} (${engine.source})` : undefined,
    ok: false,
    claims: [],
    coverage: deck.coverage,
    coveragePercent: cov.percent,
    uncoveredRails,
    problems: deck.problems,
    hardFailures: [],
  }

  if (!engine) {
    return {
      ...base,
      problems: [
        ...deck.problems,
        "ngspice is not installed — L7 did not run, so no electrical claim on this board has been checked.",
      ],
      // Not a hard failure: an uninstalled tool is an environment problem, and failing
      // the board for it would be blaming the design for the bench. It is loudly
      // reported instead, and `available: false` is visible in every artifact.
    }
  }

  // Voltage claims all read from one operating-point solve; current claims read the
  // same solve's branch currents. One `.op` answers every claim in this grammar.
  const prints: string[] = []
  const wanted = new Map<string, Claim>()
  for (const claim of claims) {
    if (isCurrentClaim(claim)) {
      // Branch current through the rail source feeding this component is not what is
      // asked; what is asked is the current in the component's own device. ngspice
      // names it after the element, which is prefixed by device letter.
      prints.push(`print @r${claim.target.toLowerCase()}[i]`)
      wanted.set(`@r${claim.target.toLowerCase()}[i]`, claim)
      continue
    }
    const node = claimNode(claim, deck.nodeOf)
    if (node === null) continue
    prints.push(`print v(${node.toLowerCase()})`)
    wanted.set(`v(${node.toLowerCase()})`, claim)
  }

  // Branch currents through a resistor are only available if they were saved before the
  // run — ngspice keeps node voltages by default and discards element internals. Asking
  // for `@rr2[i]` after the fact returns nothing, which the evaluator would otherwise
  // report as "the claim was not measured" for a claim that was perfectly well formed.
  const saves = [...wanted.keys()].filter((k) => k.startsWith("@"))
  const text = [
    deck.text,
    ".op",
    ".control",
    ...(saves.length ? [`save all ${saves.join(" ")}`] : []),
    "run",
    ...prints,
    ".endc",
    ".end",
    "",
  ].join("\n")

  const sim = await simulate({ deck: text, dir, name: "op" })
  const deckPath = path.join(dir, "op.cir")

  if (!sim.ok) {
    // Report every claim as unevaluated rather than returning an empty list. An empty
    // list reads as "nothing was claimed", which is the opposite of what happened.
    return {
      ...base,
      deckPath,
      claims: claims.map((claim) => ({
        claim,
        pass: false,
        detail: `not evaluated — the deck did not solve`,
      })),
      problems: [...deck.problems, sim.error ?? "ngspice failed with no diagnosable error"],
      hardFailures: [
        `SPICE did not solve: ${sim.error ?? "unknown failure"} — no electrical claim was verified`,
      ],
    }
  }

  const results: ClaimResult[] = []
  for (const claim of claims) {
    const key =
      isCurrentClaim(claim)
        ? `@r${claim.target.toLowerCase()}[i]`
        : (() => {
            const node = claimNode(claim, deck.nodeOf)
            return node === null ? null : `v(${node.toLowerCase()})`
          })()

    if (key === null) {
      results.push({
        claim,
        pass: false,
        detail: `"${claim.target}" is not a net in the compiled netlist — the claim names something that does not exist`,
      })
      continue
    }

    const measured = sim.values.get(key)
    if (measured === undefined) {
      results.push({
        claim,
        pass: false,
        detail: `ngspice returned no value for ${key} — the claim was not measured, so it is not verified`,
      })
      continue
    }

    const limit =
      claim.kind === "dc_rail" ? Math.abs(claim.expected * claim.tolerance) : claim.tolerance
    const delta = Math.abs(measured - claim.expected)
    // Only `current_max` is one-sided. Everything else is a window, because a
    // measurement far *below* what was expected is a defect too: an LED branch reading
    // 0 A is an open circuit or a resistor off by a decade, and a one-sided check calls
    // that a pass. (It did, until a planted 100k resistor was waved through.)
    const pass = claim.kind === "current_max" ? measured <= claim.expected + limit : delta <= limit
    const unit = isCurrentClaim(claim) ? "A" : "V"
    const tautological =
      !isCurrentClaim(claim) && drivenRails.has(claim.target)
    results.push({
      claim,
      pass,
      measured,
      tautological,
      detail:
        (pass
          ? `${claim.target} = ${measured.toFixed(4)} ${unit} (expected ${claim.expected} ±${limit.toFixed(4)})`
          : `${claim.target} = ${measured.toFixed(4)} ${unit}, expected ${claim.expected} ±${limit.toFixed(4)} — off by ${delta.toFixed(4)} ${unit}`) +
        (tautological
          ? "  [TAUTOLOGY: this net is driven by an ideal source in the deck, so it cannot read anything else. Proves the deck, not the design — the rail-under-load question is L6's.]"
          : ""),
    })
  }

  const failed = results.filter((r) => !r.pass)
  const hardFailures: string[] = []
  for (const f of failed) hardFailures.push(`claim failed: ${f.detail}`)
  if (uncoveredRails.length) {
    hardFailures.push(
      `rail(s) with no claim asserted against them: ${uncoveredRails.join(", ")} — ` +
        `unverified, not verified-good`,
    )
  }
  for (const p of deck.problems) hardFailures.push(`deck problem: ${p}`)

  return {
    ...base,
    ok: hardFailures.length === 0,
    claims: results,
    deckPath,
    hardFailures,
  }
}

export function describeSpice(report: SpiceReport): string {
  const lines: string[] = []
  if (!report.available) {
    lines.push("SPICE  NOT RUN — ngspice is not installed.")
    lines.push(...report.problems.map((p) => `  ${p}`))
    return lines.join("\n")
  }

  lines.push(`SPICE  ${report.engine}`)
  const cov = coverageSummary(report.coverage)
  lines.push(
    `  model coverage ${report.coveragePercent.toFixed(0)}% — ` +
      `${cov.modelled} modelled, ${cov.stubbed} behavioural stub(s), ${cov.skipped} not represented`,
  )

  const substantive = report.claims.filter((r) => !r.tautological)
  lines.push(
    "",
    `  claims (${report.claims.length}, of which ${substantive.length} can actually fail):`,
  )
  for (const r of report.claims) {
    lines.push(`    ${r.pass ? "PASS" : "FAIL"}  ${r.detail}`)
    if (!r.pass) lines.push(`          why it matters: ${r.claim.why}`)
  }
  if (!report.claims.length) lines.push("    none asserted")
  else if (!substantive.length) {
    lines.push(
      "",
      "  WARNING: every claim is a tautology. This stage measured nothing about the",
      "  design. Add claims on nets the deck does not drive — bias networks, pull-ups,",
      "  current-limiting branches — or L7 is decoration.",
    )
  }

  if (report.uncoveredRails.length) {
    lines.push("", `  UNCOVERED RAILS: ${report.uncoveredRails.join(", ")}`)
  }

  const stubbed = report.coverage.filter((c) => c.coverage === "stubbed")
  if (stubbed.length) {
    lines.push("", "  behavioural stubs (their behaviour is asserted, not simulated):")
    for (const s of stubbed) lines.push(`    ${s.ref}  ${s.note}`)
  }

  const skipped = report.coverage.filter((c) => c.coverage === "skipped")
  if (skipped.length) {
    lines.push("", "  not represented in the deck:")
    for (const s of skipped) lines.push(`    ${s.ref}  ${s.note}`)
  }

  if (report.problems.length) {
    lines.push("", "  problems:")
    for (const p of report.problems) lines.push(`    ${p}`)
  }

  return lines.join("\n")
}
