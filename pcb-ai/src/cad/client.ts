/**
 * Typed client for the cad-generation service, plus the §6 negotiation loop.
 *
 * The client is a thin, validated wrapper — every response is parsed through the
 * zod contract so a drift between the Python service and this file fails loudly
 * at the boundary rather than silently producing a wrong enclosure.
 *
 * `negotiate()` implements §6's loop verbatim, including the parts that are easy
 * to skip: the 3-round hard stop, and reporting a non-converged pair **as
 * non-converged** rather than returning the last attempt as though it worked.
 */

import {
  BoardReport,
  DesignEnclosureResponse,
  EnclosureIntent,
  EnclosureReport,
  Envelope,
  FitResult,
  Violation,
} from "./contracts.ts"

export interface CadClientOptions {
  /** Base URL of the cad-generation service. */
  baseUrl?: string
  /** Per-request timeout in ms. */
  timeoutMs?: number
  fetchImpl?: typeof fetch
}

export class CadServiceError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: string,
  ) {
    super(message)
    this.name = "CadServiceError"
  }
}

export class CadClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private readonly fetchImpl: typeof fetch

  constructor(opts: CadClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? process.env.CAD_API_URL ?? "http://127.0.0.1:8200").replace(
      /\/+$/,
      "",
    )
    this.timeoutMs = opts.timeoutMs ?? 120_000
    this.fetchImpl = opts.fetchImpl ?? fetch
  }

  private async call<T>(path: string, body: unknown, parse: { parse: (x: unknown) => T }): Promise<T> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    let res: Response
    try {
      res = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } catch (e) {
      throw new CadServiceError(
        `cad-generation unreachable at ${this.baseUrl}${path}: ${(e as Error).message}`,
        0,
        "",
      )
    } finally {
      clearTimeout(timer)
    }

    const text = await res.text()
    if (!res.ok) {
      throw new CadServiceError(`cad-generation ${path} returned ${res.status}`, res.status, text)
    }
    return parse.parse(JSON.parse(text))
  }

  async health(): Promise<{ ok: boolean; generators: string[] }> {
    const res = await this.fetchImpl(`${this.baseUrl}/health`)
    if (!res.ok) throw new CadServiceError("health check failed", res.status, await res.text())
    return (await res.json()) as { ok: boolean; generators: string[] }
  }

  /** §6 `cad.design_enclosure(board_report, intent)`. */
  async designEnclosure(
    board_report: BoardReport,
    intent?: Partial<EnclosureIntent>,
    emit_artifacts = true,
  ): Promise<DesignEnclosureResponse> {
    return this.call(
      "/cad/design_enclosure",
      { board_report, intent: intent ?? {}, emit_artifacts },
      DesignEnclosureResponse,
    )
  }

  /** §6 `cad.check_fit(board_report)` — deterministic geometry, no model in the loop. */
  async checkFit(board_report: BoardReport, enclosure_report: EnclosureReport): Promise<FitResult> {
    return this.call("/cad/check_fit", { board_report, enclosure_report }, FitResult)
  }

  /** §6 `cad.constrain_board(reason)`. */
  async constrainBoard(
    reason: string,
    enclosure_report: EnclosureReport,
    intent?: Partial<EnclosureIntent>,
  ): Promise<Envelope> {
    return this.call(
      "/cad/constrain_board",
      { reason, enclosure_report, intent: intent ?? {} },
      Envelope,
    )
  }
}

// --- the §6 negotiation loop --------------------------------------------

/**
 * The PCB side's half of the contract: re-place/re-route inside a CAD-imposed
 * envelope. §6 calls this `pcb.replace_within(envelope) -> new board_report`.
 *
 * pcb-ai does not implement this yet. Until it does, `negotiate()` runs one
 * round and reports non-convergence honestly rather than pretending to converge.
 */
export type ReplaceWithin = (envelope: Envelope, current: BoardReport) => Promise<BoardReport>

export interface NegotiationRound {
  round: number
  boardReport: BoardReport
  enclosureReport: EnclosureReport
  fit: FitResult
  envelope?: Envelope
  movedSide?: "pcb" | "cad"
}

export interface NegotiationResult {
  converged: boolean
  rounds: NegotiationRound[]
  /** Present only when `converged` is true. */
  final?: { boardReport: BoardReport; enclosureReport: EnclosureReport }
  /** Why it stopped, in plain terms — surfaced to the user, never swallowed. */
  reason: string
  outstandingViolations: Violation[]
}

export const MAX_ROUNDS = 3

/**
 * Drive PCB <-> CAD to a fit, or report that it didn't reach one.
 *
 * This is a deterministic negotiator: no model decides anything here. It reads
 * violation magnitudes and applies §6's rule — the side with more freedom moves
 * first, which in practice is the PCB (re-placing parts is cheaper than
 * regrowing a box around them).
 */
export async function negotiate(
  cad: CadClient,
  initialBoard: BoardReport,
  opts: {
    intent?: Partial<EnclosureIntent>
    replaceWithin?: ReplaceWithin
    maxRounds?: number
    emitArtifacts?: boolean
  } = {},
): Promise<NegotiationResult> {
  const maxRounds = opts.maxRounds ?? MAX_ROUNDS
  const rounds: NegotiationRound[] = []
  let board = initialBoard

  for (let i = 1; i <= maxRounds; i++) {
    const designed = await cad.designEnclosure(board, opts.intent, opts.emitArtifacts ?? false)
    const enclosure = designed.enclosure_report
    const fit = designed.fit

    const round: NegotiationRound = { round: i, boardReport: board, enclosureReport: enclosure, fit }
    rounds.push(round)

    const blocking = fit.violations.filter(
      (v) => v.severity === "blocker" || v.severity === "major",
    )
    if (blocking.length === 0) {
      return {
        converged: true,
        rounds,
        final: { boardReport: board, enclosureReport: enclosure },
        reason:
          i === 1
            ? "fit on the first attempt; no negotiation was needed"
            : `converged after ${i} round(s)`,
        outstandingViolations: fit.violations,
      }
    }

    if (!opts.replaceWithin) {
      return {
        converged: false,
        rounds,
        reason:
          "the enclosure does not fit the board, and no `replaceWithin` was supplied, so the " +
          "PCB side cannot move. Implement pcb.replace_within (§6) and pass it to negotiate().",
        outstandingViolations: fit.violations,
      }
    }

    const worst = [...blocking].sort((a, b) => overshoot(b) - overshoot(a))[0]
    const envelope = await cad.constrainBoard(
      `round ${i}: ${worst.code} — ${worst.detail}`,
      enclosure,
      opts.intent,
    )
    round.envelope = envelope
    round.movedSide = "pcb"
    board = await opts.replaceWithin(envelope, board)
  }

  const last = rounds[rounds.length - 1]
  return {
    converged: false,
    rounds,
    reason:
      `hard stop at ${maxRounds} rounds without a fit (§6). This pair is reported as ` +
      `non-converged; it has not been quietly accepted.`,
    outstandingViolations: last.fit.violations,
  }
}

/** How far past its limit a violation is. Mirrors `Violation.overshoot` in Python. */
export function overshoot(v: Violation): number {
  return v.measured - v.limit
}

/** Human-readable negotiation transcript for run logs. */
export function describeNegotiation(result: NegotiationResult): string {
  const lines: string[] = []
  lines.push(result.converged ? `PCB<->CAD: CONVERGED — ${result.reason}` : `PCB<->CAD: NOT CONVERGED — ${result.reason}`)
  for (const r of result.rounds) {
    const blocking = r.fit.violations.filter((v) => v.severity !== "minor").length
    lines.push(
      `  round ${r.round}: cavity ${r.enclosureReport.cavity_mm.length_mm.toFixed(1)}x` +
        `${r.enclosureReport.cavity_mm.width_mm.toFixed(1)}x${r.enclosureReport.cavity_mm.height_mm.toFixed(1)}mm` +
        `  violations=${r.fit.violations.length} (blocking ${blocking})`,
    )
    for (const v of r.fit.violations) {
      lines.push(
        `      [${v.severity}] ${v.code} ${v.ref ? `(${v.ref}) ` : ""}` +
          `measured=${v.measured.toFixed(3)}${v.unit} limit=${v.limit.toFixed(3)}${v.unit}`,
      )
    }
  }
  return lines.join("\n")
}
