/**
 * L8 — design for manufacture.
 *
 * L5's DRC asks "is this board internally consistent". L8 asks a different and blunter
 * question: **will the fab that is going to build it accept it?** A board can be
 * perfectly self-consistent and still specify a 0.05 mm annular ring that no
 * two-layer house will quote.
 *
 * Every check below is a measurement against a number from the profile, and every
 * violation names the element, the measured value, the required value and the margin.
 * "Via V12 annular ring 0.050 mm, needs 0.100 mm" is a defect a designer can fix; "DFM
 * failed" is not.
 *
 * What is checked here and not elsewhere: this reads *fab limits*, so it deliberately
 * repeats nothing L5 already does with tscircuit's own DRC (trace-to-trace spacing on
 * the routed geometry). The overlap that does exist — two engines disagreeing about the
 * same clearance — is the point of principle 4, not an accident.
 */
import type { FabProfile } from "./profile.ts"
import { DEFAULT_PROFILE } from "./profile.ts"

export type DfmSeverity = "error" | "warning" | "ignore"

export interface DfmViolation {
  rule: string
  severity: DfmSeverity
  element: string
  measured: number
  required: number
  unit: string
  message: string
}

export interface DfmReport {
  profile: FabProfile
  violations: DfmViolation[]
  checked: Record<string, number>
  errors: number
  warnings: number
}

const mm = (n: number) => n.toFixed(3)

/** Distance between two points. */
const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by)

export function runDfm(circuitJson: any[], profile: FabProfile = DEFAULT_PROFILE): DfmReport {
  const violations: DfmViolation[] = []
  const checked: Record<string, number> = {}

  const byType = (t: string) => circuitJson.filter((e: any) => e?.type === t)
  const severityOf = (rule: string): DfmSeverity =>
    (profile.severities[rule] as DfmSeverity) ?? "error"

  const add = (
    rule: string,
    element: string,
    measured: number,
    required: number,
    message: string,
    unit = "mm",
  ) => {
    const severity = severityOf(rule)
    if (severity === "ignore") return
    violations.push({ rule, severity, element, measured, required, unit, message })
  }

  // ── Track width ──────────────────────────────────────────────────────────────
  //
  // Reported per distinct width rather than per segment: a board routed at 0.15 mm has
  // hundreds of segments at that width, and hundreds of identical violations is a wall
  // of text that hides the other findings.
  const traces = byType("pcb_trace")
  const narrow = new Map<number, number>()
  let segments = 0
  for (const t of traces as any[]) {
    for (const r of t.route ?? []) {
      if (typeof r.width !== "number") continue
      segments++
      if (r.width < profile.min_track_width - 1e-9) {
        narrow.set(r.width, (narrow.get(r.width) ?? 0) + 1)
      }
    }
  }
  checked["track segments"] = segments
  for (const [width, count] of [...narrow.entries()].sort((a, b) => a[0] - b[0])) {
    add(
      "track_width",
      `${count} segment(s)`,
      width,
      profile.min_track_width,
      `${count} track segment(s) at ${mm(width)} mm, below the ${mm(profile.min_track_width)} mm minimum`,
    )
  }

  // ── Vias: outer diameter, drill, annular ring ────────────────────────────────
  const vias = byType("pcb_via") as any[]
  checked["vias"] = vias.length
  const viaGroups = new Map<string, { outer: number; hole: number; ids: string[] }>()
  for (const v of vias) {
    const key = `${v.outer_diameter}|${v.hole_diameter}`
    const g = viaGroups.get(key) ?? {
      outer: v.outer_diameter,
      hole: v.hole_diameter,
      ids: [] as string[],
    }
    g.ids.push(v.pcb_via_id)
    viaGroups.set(key, g)
  }
  for (const g of viaGroups.values()) {
    const label = `${g.ids.length} via(s)`
    if (g.outer < profile.min_via_diameter - 1e-9) {
      add(
        "via_diameter",
        label,
        g.outer,
        profile.min_via_diameter,
        `${g.ids.length} via(s) with ${mm(g.outer)} mm pad diameter, below the ${mm(profile.min_via_diameter)} mm minimum`,
      )
    }
    if (g.hole < profile.min_through_hole_diameter - 1e-9) {
      add(
        "via_drill",
        label,
        g.hole,
        profile.min_through_hole_diameter,
        `${g.ids.length} via(s) drilled ${mm(g.hole)} mm, below the ${mm(profile.min_through_hole_diameter)} mm minimum`,
      )
    }
    const annular = (g.outer - g.hole) / 2
    if (annular < profile.min_via_annular_width - 1e-9) {
      add(
        "annular_width",
        label,
        annular,
        profile.min_via_annular_width,
        `${g.ids.length} via(s) with a ${mm(annular)} mm annular ring (${mm(g.outer)} mm pad on a ` +
          `${mm(g.hole)} mm drill), below the ${mm(profile.min_via_annular_width)} mm minimum — ` +
          `the drill can break out of the pad`,
      )
    }
  }

  // ── Plated holes ─────────────────────────────────────────────────────────────
  const holes = byType("pcb_plated_hole") as any[]
  checked["plated holes"] = holes.length
  for (const h of holes) {
    const drill = h.hole_diameter ?? h.hole_width
    if (typeof drill === "number" && drill < profile.min_through_hole_diameter - 1e-9) {
      add(
        "hole_size",
        h.pcb_plated_hole_id ?? "plated hole",
        drill,
        profile.min_through_hole_diameter,
        `plated hole drilled ${mm(drill)} mm, below the ${mm(profile.min_through_hole_diameter)} mm minimum`,
      )
    }
  }

  // ── Hole to hole ─────────────────────────────────────────────────────────────
  //
  // O(n²) over holes. At a few hundred holes that is tens of thousands of comparisons —
  // microseconds — and worth far more than the cleverness of a spatial index here.
  const allHoles: Array<{ id: string; x: number; y: number; r: number }> = [
    ...vias.map((v) => ({
      id: v.pcb_via_id,
      x: v.x,
      y: v.y,
      r: (v.hole_diameter ?? 0) / 2,
    })),
    ...holes.map((h) => ({
      id: h.pcb_plated_hole_id ?? "hole",
      x: h.x,
      y: h.y,
      r: (h.hole_diameter ?? h.hole_width ?? 0) / 2,
    })),
  ].filter((h) => Number.isFinite(h.x) && Number.isFinite(h.y) && h.r > 0)

  let worstPair: { a: string; b: string; gap: number } | null = null
  for (let i = 0; i < allHoles.length; i++) {
    for (let j = i + 1; j < allHoles.length; j++) {
      const a = allHoles[i]
      const b = allHoles[j]
      const gap = dist(a.x, a.y, b.x, b.y) - a.r - b.r
      if (gap < profile.min_hole_to_hole - 1e-9) {
        if (!worstPair || gap < worstPair.gap) worstPair = { a: a.id, b: b.id, gap }
      }
    }
  }
  if (worstPair) {
    add(
      "hole_to_hole",
      `${worstPair.a} ↔ ${worstPair.b}`,
      worstPair.gap,
      profile.min_hole_to_hole,
      `closest hole-to-hole gap is ${mm(worstPair.gap)} mm (${worstPair.a} to ${worstPair.b}), ` +
        `below the ${mm(profile.min_hole_to_hole)} mm minimum`,
    )
  }

  // ── Copper to board edge ─────────────────────────────────────────────────────
  const board: any = byType("pcb_board")[0]
  if (board) {
    const halfW = board.width / 2
    const halfH = board.height / 2
    const bx = board.center?.x ?? 0
    const by = board.center?.y ?? 0
    const edgeGap = (x: number, y: number, r: number) =>
      Math.min(x - r - (bx - halfW), bx + halfW - (x + r), y - r - (by - halfH), by + halfH - (y + r))

    let worstEdge: { id: string; gap: number } | null = null
    const consider = (id: string, x: number, y: number, r: number) => {
      if (!Number.isFinite(x) || !Number.isFinite(y)) return
      const gap = edgeGap(x, y, r)
      if (gap < profile.min_copper_edge_clearance - 1e-9) {
        if (!worstEdge || gap < worstEdge.gap) worstEdge = { id, gap }
      }
    }
    for (const v of vias) consider(v.pcb_via_id, v.x, v.y, (v.outer_diameter ?? 0) / 2)
    for (const p of byType("pcb_smtpad") as any[]) {
      const r = Math.max(p.width ?? p.radius ?? 0, p.height ?? p.radius ?? 0) / 2
      consider(p.pcb_smtpad_id ?? "pad", p.x, p.y, r)
    }
    if (worstEdge) {
      const w = worstEdge as { id: string; gap: number }
      add(
        "copper_edge_clearance",
        w.id,
        w.gap,
        profile.min_copper_edge_clearance,
        `copper comes within ${mm(w.gap)} mm of the board edge (${w.id}), ` +
          `below the ${mm(profile.min_copper_edge_clearance)} mm minimum — the router bit will cut it`,
      )
    }
    checked["board"] = 1
  }

  // ── Silkscreen legibility ────────────────────────────────────────────────────
  const silk = byType("pcb_silkscreen_text") as any[]
  checked["silkscreen texts"] = silk.length
  const small = new Map<number, number>()
  for (const s of silk) {
    const h = s.font_size
    if (typeof h === "number" && h < profile.min_text_height - 1e-9) {
      small.set(h, (small.get(h) ?? 0) + 1)
    }
  }
  for (const [height, count] of [...small.entries()].sort((a, b) => a[0] - b[0])) {
    add(
      "text_height",
      `${count} text item(s)`,
      height,
      profile.min_text_height,
      `${count} silkscreen text(s) ${mm(height)} mm tall, below the ${mm(profile.min_text_height)} mm ` +
        `minimum — will not print legibly`,
    )
  }

  const errors = violations.filter((v) => v.severity === "error").length
  const warnings = violations.filter((v) => v.severity === "warning").length
  return { profile, violations, checked, errors, warnings }
}

export function describeDfm(report: DfmReport): string {
  const lines: string[] = []
  lines.push(`DFM  against "${report.profile.name}" (${report.profile.source})`)
  lines.push(
    `  checked: ${Object.entries(report.checked)
      .map(([k, v]) => `${v} ${k}`)
      .join(", ")}`,
  )
  lines.push("")
  if (!report.violations.length) {
    lines.push("  no violations.")
    return lines.join("\n")
  }
  lines.push(`  ${report.errors} error(s), ${report.warnings} warning(s):`)
  for (const v of report.violations) {
    lines.push(
      `    ${v.severity === "error" ? "ERROR  " : "WARNING"} [${v.rule}] ${v.message}`,
    )
  }
  return lines.join("\n")
}

/** Errors only — what may block promotion. Warnings are reported, never gating. */
export function dfmBlockers(report: DfmReport): string[] {
  return report.violations
    .filter((v) => v.severity === "error")
    .map((v) => `DFM [${v.rule}]: ${v.message}`)
}
