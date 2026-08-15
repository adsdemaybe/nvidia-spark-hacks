/**
 * Checking placement rules against the board that was actually produced.
 *
 * Every answer here is geometry measured from Circuit JSON. No rule is satisfied
 * because an agent says so, and a rule that names a part which does not exist is a
 * failure rather than a skip — a typo'd designator must not silently disable a gate.
 */
import type { Edge, PlacementRule } from "./constraints.ts"
import { EDGES } from "./constraints.ts"

export interface PlacedPart {
  ref: string
  layer: string
  /** Courtyard if the footprint has one, body extents otherwise. */
  cx: number
  cy: number
  w: number
  h: number
  /** Clearance to each board edge, in mm. Negative means it hangs off. */
  gaps: Record<Edge, number>
  /** The edge it is closest to. */
  nearest: Edge
  nearestGap: number
}

export interface PlacementViolation {
  rule: PlacementRule
  message: string
}

export interface PlacementReport {
  parts: PlacedPart[]
  violations: PlacementViolation[]
  checked: number
  /** True when the board has no rules to check — reported, never treated as a pass. */
  unchecked: boolean
}

const mm = (n: number) => (Math.round(n * 100) / 100).toFixed(2)

/** Which edge is opposite which. */
const OPPOSITE: Record<Edge, Edge> = {
  left: "right",
  right: "left",
  top: "bottom",
  bottom: "top",
}

const DEFAULT_EDGE_TOLERANCE_MM = 3
const DEFAULT_ROW_SPREAD_MM = 1

export function extractPlacement(circuitJson: any[]): PlacedPart[] {
  const byType = (t: string) => circuitJson.filter((e: any) => e?.type === t)
  const board: any = byType("pcb_board")[0]
  if (!board) return []

  const halfW = board.width / 2
  const halfH = board.height / 2
  const bx = board.center?.x ?? 0
  const by = board.center?.y ?? 0

  const sources = new Map<string, any>(
    byType("source_component").map((e: any) => [e.source_component_id, e]),
  )
  const courtyards = new Map<string, any>(
    byType("pcb_courtyard_rect").map((c: any) => [c.pcb_component_id, c]),
  )

  return byType("pcb_component").map((c: any) => {
    const src = sources.get(c.source_component_id) ?? {}
    const court = courtyards.get(c.pcb_component_id)
    const cx = court ? court.center.x : c.center.x
    const cy = court ? court.center.y : c.center.y
    const w = court ? court.width : c.width
    const h = court ? court.height : c.height

    const gaps: Record<Edge, number> = {
      left: cx - w / 2 - (bx - halfW),
      right: bx + halfW - (cx + w / 2),
      bottom: cy - h / 2 - (by - halfH),
      top: by + halfH - (cy + h / 2),
    }
    let nearest: Edge = "left"
    for (const e of EDGES) if (gaps[e] < gaps[nearest]) nearest = e

    return {
      ref: src.name ?? c.pcb_component_id,
      layer: String(c.layer ?? "top"),
      cx,
      cy,
      w,
      h,
      gaps,
      nearest,
      nearestGap: gaps[nearest],
    }
  })
}

export function checkPlacement(
  circuitJson: any[],
  rules: PlacementRule[],
): PlacementReport {
  const parts = extractPlacement(circuitJson)
  const byRef = new Map(parts.map((p) => [p.ref, p]))
  const violations: PlacementViolation[] = []

  const fail = (rule: PlacementRule, message: string) => violations.push({ rule, message })

  /** Resolve a rule's refs, failing the rule if any names nothing on the board. */
  const resolve = (rule: PlacementRule): PlacedPart[] | null => {
    if (rule.refs.length === 1 && rule.refs[0] === "*") return parts
    const found: PlacedPart[] = []
    for (const ref of rule.refs) {
      const part = byRef.get(ref)
      if (!part) {
        fail(
          rule,
          `names "${ref}", which is not a component on this board — the rule could not be ` +
            `checked, so it is reported as failed rather than skipped`,
        )
        return null
      }
      found.push(part)
    }
    return found
  }

  for (const rule of rules) {
    const targets = resolve(rule)
    if (!targets) continue
    const tol = rule.max_mm ?? DEFAULT_EDGE_TOLERANCE_MM

    switch (rule.kind) {
      case "at_edge": {
        for (const p of targets) {
          if (rule.edge && rule.edge !== "any") {
            const gap = p.gaps[rule.edge as Edge]
            if (gap > tol) {
              fail(
                rule,
                `${p.ref} is ${mm(gap)} mm from the ${rule.edge} edge (limit ${mm(tol)} mm). ` +
                  `It is closest to the ${p.nearest} edge at ${mm(p.nearestGap)} mm` +
                  (p.nearest === OPPOSITE[rule.edge as Edge]
                    ? " — the opposite edge of the outline."
                    : "."),
              )
            }
          } else if (p.nearestGap > tol) {
            fail(
              rule,
              `${p.ref} is ${mm(p.nearestGap)} mm from its nearest edge (${p.nearest}), ` +
                `limit ${mm(tol)} mm — it is sitting in the interior.`,
            )
          }
        }
        break
      }

      case "opposite_edges": {
        if (targets.length !== 2) {
          fail(rule, `needs exactly two parts, got ${targets.length}`)
          break
        }
        const [a, b] = targets
        if (a.nearest !== OPPOSITE[b.nearest]) {
          fail(
            rule,
            `${a.ref} is on the ${a.nearest} edge and ${b.ref} is on the ${b.nearest} edge — ` +
              (a.nearest === b.nearest
                ? `both against the same edge, not opposing ones.`
                : `adjacent edges, not opposing ones.`),
          )
        }
        for (const p of targets) {
          if (p.nearestGap > tol) {
            fail(
              rule,
              `${p.ref} is ${mm(p.nearestGap)} mm from the ${p.nearest} edge (limit ${mm(tol)} mm) ` +
                `— it is not at an edge at all.`,
            )
          }
        }
        break
      }

      case "same_edge": {
        const edges = new Set(targets.map((p) => p.nearest))
        if (edges.size > 1) {
          fail(
            rule,
            `expected one shared edge, found ${targets
              .map((p) => `${p.ref} on ${p.nearest}`)
              .join(", ")}`,
          )
        }
        if (rule.edge && rule.edge !== "any") {
          for (const p of targets) {
            if (p.nearest !== rule.edge) {
              fail(rule, `${p.ref} is on the ${p.nearest} edge, expected ${rule.edge}`)
            }
          }
        }
        break
      }

      case "on_layer": {
        const want = rule.layer ?? "top"
        const wrong = targets.filter((p) => p.layer !== want)
        if (wrong.length) {
          fail(
            rule,
            `${wrong.length} part(s) mounted on the wrong copper side: ` +
              `${wrong.map((p) => `${p.ref} (${p.layer} side)`).join(", ")} — expected the ` +
              `${want} side. This is which FACE of the board the part is soldered to, not ` +
              `which edge of the outline it sits near.`,
          )
        }
        break
      }

      case "adjacent": {
        if (targets.length !== 2) {
          fail(rule, `needs exactly two parts, got ${targets.length}`)
          break
        }
        const [a, b] = targets
        const d = Math.hypot(a.cx - b.cx, a.cy - b.cy)
        const limit = rule.max_mm ?? 5
        if (d > limit) {
          fail(rule, `${a.ref} and ${b.ref} are ${mm(d)} mm apart, limit ${mm(limit)} mm`)
        }
        break
      }

      case "in_row": {
        if (targets.length < 2) {
          fail(rule, `needs at least two parts, got ${targets.length}`)
          break
        }
        // Lining up "along x" means the parts vary in x and share a y.
        const axis = rule.axis ?? "x"
        const across = targets.map((p) => (axis === "x" ? p.cy : p.cx))
        const spread = Math.max(...across) - Math.min(...across)
        const limit = rule.max_mm ?? DEFAULT_ROW_SPREAD_MM
        if (spread > limit) {
          fail(
            rule,
            `${targets.map((p) => p.ref).join(", ")} vary by ${mm(spread)} mm across the ` +
              `${axis === "x" ? "y" : "x"} axis, limit ${mm(limit)} mm — they are not in a row`,
          )
        }
        break
      }
    }
  }

  return {
    parts,
    violations,
    checked: rules.length,
    unchecked: rules.length === 0,
  }
}

export function describePlacement(report: PlacementReport): string {
  const lines: string[] = []

  if (report.unchecked) {
    lines.push(
      "PLACEMENT  no machine-checkable rules were emitted for this board, so nothing " +
        "about where the parts sit has been verified.",
    )
  } else {
    lines.push(
      `PLACEMENT  ${report.checked} rule(s) checked, ${report.violations.length} violation(s).`,
    )
  }

  const connectors = report.parts.filter((p) => /^(J|P|CN|SW)\d/i.test(p.ref))
  if (connectors.length) {
    lines.push(
      "",
      "  which OUTLINE EDGE each connector sits near (in-plane; not the copper side):",
    )
    for (const c of connectors) {
      lines.push(
        `    ${c.ref.padEnd(6)} ${c.nearest.padEnd(6)} edge, ${mm(c.nearestGap).padStart(7)} mm clear` +
          `   (left ${mm(c.gaps.left)}, right ${mm(c.gaps.right)}, top ${mm(c.gaps.top)}, bottom ${mm(c.gaps.bottom)})`,
      )
    }
  }

  const bottom = report.parts.filter((p) => p.layer !== "top")
  if (bottom.length) {
    lines.push(
      "",
      `  ${bottom.length} part(s) mounted on the BOTTOM COPPER SIDE: ` +
        `${bottom.map((p) => p.ref).join(", ")}. This is a legitimate choice — ` +
        `bottom-side connectors are common — and is only a defect if the specification ` +
        `asked for single-sided assembly.`,
    )
  }

  if (report.violations.length) {
    lines.push("", "  violations:")
    for (const v of report.violations) {
      lines.push(`    [${v.rule.kind}] ${v.message}`)
      lines.push(`      why it matters: ${v.rule.why}`)
    }
  }

  return lines.join("\n")
}

/** Violations that must block promotion. */
export function placementBlockers(report: PlacementReport): string[] {
  return report.violations.map((v) => `placement [${v.rule.kind}]: ${v.message}`)
}
