/**
 * A measured description of the board's geometry, for reviewers that cannot see.
 *
 * The layout, spec and physics reviewers were specified against rendered PNGs. Laguna
 * — the model this pipeline runs locally on the Spark — is a coding model with no
 * vision, and it is not the only useful text-only model. Dropping the images silently
 * would leave those agents reviewing from nothing while still sounding confident, which
 * is the exact failure this pipeline is built to prevent.
 *
 * So the images are replaced by measurement. Every fact below is computed from the
 * Circuit JSON the views are themselves drawn from: placements, courtyard overlaps,
 * edge clearances, routing detours, silkscreen. Nothing here is inferred and nothing is
 * rounded beyond display precision.
 *
 * This is not a downgrade for a text model to tolerate. A number is a better review
 * input than a picture of a number — it is diffable between iterations, it cannot be
 * misread, and a finding raised against it can be checked. The vision path stays for
 * models that have it; this path is what a reviewer can actually be held to.
 */
import type { BuildResult } from "./types.ts"

interface Rect {
  cx: number
  cy: number
  w: number
  h: number
}

const mm = (n: number) => (Math.round(n * 100) / 100).toFixed(2)

function overlap(a: Rect, b: Rect): { x: number; y: number } | null {
  const x = Math.min(a.cx + a.w / 2, b.cx + b.w / 2) - Math.max(a.cx - a.w / 2, b.cx - b.w / 2)
  const y = Math.min(a.cy + a.h / 2, b.cy + b.h / 2) - Math.max(a.cy - a.h / 2, b.cy - b.h / 2)
  return x > 0 && y > 0 ? { x, y } : null
}

/** Straight-line distance between two points. */
const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by)

/**
 * Component reference designators that are connectors — the parts a layout reviewer
 * expects to find at a board edge rather than buried in the middle.
 */
const CONNECTOR_FTYPES = new Set([
  "simple_pin_header",
  "simple_connector",
  "simple_push_button",
  "simple_switch",
])

export function describeLayout(build: BuildResult): string {
  const json = build.circuitJson ?? []
  if (!json.length) return "No compiled geometry available."

  const byType = <T = any>(t: string): T[] => json.filter((e: any) => e?.type === t) as T[]

  const board: any = byType("pcb_board")[0]
  const sources = new Map<string, any>(
    byType("source_component").map((e: any) => [e.source_component_id, e]),
  )
  const pcbComponents = byType("pcb_component")
  const courtyards = byType("pcb_courtyard_rect")
  const courtyardByComponent = new Map<string, any>(
    courtyards.map((c: any) => [c.pcb_component_id, c]),
  )

  const out: string[] = []

  // ── Board ────────────────────────────────────────────────────────────────────
  if (board) {
    out.push(
      `BOARD  ${mm(board.width)} x ${mm(board.height)} mm, ${board.num_layers} layer(s), ` +
        `${mm(board.thickness)} mm ${board.material ?? "fr4"}, centred at ` +
        `(${mm(board.center?.x ?? 0)}, ${mm(board.center?.y ?? 0)}). ` +
        `Area ${mm(board.width * board.height)} mm².`,
    )
  }

  const halfW = board ? board.width / 2 : 0
  const halfH = board ? board.height / 2 : 0
  const bx = board?.center?.x ?? 0
  const by = board?.center?.y ?? 0
  const edgeClearance = (r: Rect) =>
    board
      ? Math.min(
          r.cx - r.w / 2 - (bx - halfW),
          bx + halfW - (r.cx + r.w / 2),
          r.cy - r.h / 2 - (by - halfH),
          by + halfH - (r.cy + r.h / 2),
        )
      : Number.NaN

  // ── Placement ────────────────────────────────────────────────────────────────
  const placed = pcbComponents.map((c: any) => {
    const src = sources.get(c.source_component_id) ?? {}
    const court = courtyardByComponent.get(c.pcb_component_id)
    const rect: Rect = court
      ? { cx: court.center.x, cy: court.center.y, w: court.width, h: court.height }
      : { cx: c.center.x, cy: c.center.y, w: c.width, h: c.height }
    return {
      id: c.pcb_component_id,
      name: src.name ?? c.pcb_component_id,
      ftype: src.ftype ?? "",
      layer: c.layer,
      rotation: c.rotation ?? 0,
      body: { cx: c.center.x, cy: c.center.y, w: c.width, h: c.height } as Rect,
      court: rect,
      hasCourtyard: Boolean(court),
      edge: edgeClearance(rect),
    }
  })
  placed.sort((a, b) => a.name.localeCompare(b.name, "en"))

  out.push("")
  out.push(`PLACEMENT  ${placed.length} component(s). Coordinates are the part centre, mm.`)
  out.push("  ref      layer   x        y        rot   body wxh          courtyard wxh     edge gap")
  for (const p of placed) {
    out.push(
      `  ${p.name.padEnd(8)} ${String(p.layer).padEnd(7)} ` +
        `${mm(p.body.cx).padStart(8)} ${mm(p.body.cy).padStart(8)} ` +
        `${String(p.rotation).padStart(4)}°  ` +
        `${(mm(p.body.w) + "x" + mm(p.body.h)).padEnd(16)} ` +
        `${(p.hasCourtyard ? mm(p.court.w) + "x" + mm(p.court.h) : "—").padEnd(17)} ` +
        `${Number.isFinite(p.edge) ? mm(p.edge) : "—"}`,
    )
  }

  // ── Courtyard overlaps ───────────────────────────────────────────────────────
  const collisions: string[] = []
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      const a = placed[i]
      const b = placed[j]
      if (a.layer !== b.layer) continue
      const o = overlap(a.court, b.court)
      if (o) {
        collisions.push(
          `  ${a.name} and ${b.name} courtyards overlap by ${mm(o.x)} x ${mm(o.y)} mm on ${a.layer}`,
        )
      }
    }
  }
  out.push("")
  out.push(
    collisions.length
      ? `COURTYARD OVERLAPS  ${collisions.length} pair(s) — parts too close to assemble:`
      : "COURTYARD OVERLAPS  none.",
  )
  out.push(...collisions)

  // ── Off-board and edge-hugging parts ─────────────────────────────────────────
  const offBoard = placed.filter((p) => Number.isFinite(p.edge) && p.edge < 0)
  const tight = placed.filter((p) => Number.isFinite(p.edge) && p.edge >= 0 && p.edge < 0.5)
  out.push("")
  out.push(
    offBoard.length
      ? `OFF BOARD  ${offBoard.map((p) => `${p.name} (${mm(-p.edge)} mm outside)`).join(", ")}`
      : "OFF BOARD  none.",
  )
  if (tight.length) {
    out.push(`WITHIN 0.5 mm OF EDGE  ${tight.map((p) => `${p.name} (${mm(p.edge)})`).join(", ")}`)
  }

  // ── Connectors: are they reachable from outside? ─────────────────────────────
  const connectors = placed.filter((p) => CONNECTOR_FTYPES.has(p.ftype))
  if (connectors.length && board) {
    out.push("")
    // Naming the edge, not just the distance. "J2 is 4.42 mm from an edge" does not tell
    // a reviewer that J1 and J2 are on the *same* side when the spec wanted opposite
    // ones — which is a defect that routes, simulates and fabricates perfectly well.
    out.push(
      "CONNECTOR ACCESS  which outline edge each connector sits on (compass points; " +
        "the copper side is reported separately), and its clearance:",
    )
    for (const c of connectors) {
      // Compass points, matching src/placement/check.ts and src/cad/contracts.ts.
      // "top"/"bottom" are reserved for the copper side a part is soldered to; using
      // them for edges too is what made an earlier report misread as "the connectors
      // are on the bottom of the board".
      const gaps: Record<string, number> = {
        west: c.court.cx - c.court.w / 2 - (bx - halfW),
        east: bx + halfW - (c.court.cx + c.court.w / 2),
        south: c.court.cy - c.court.h / 2 - (by - halfH),
        north: by + halfH - (c.court.cy + c.court.h / 2),
      }
      let nearest = "west"
      for (const e of Object.keys(gaps)) if (gaps[e] < gaps[nearest]) nearest = e
      const verdict = gaps[nearest] <= 2 ? "at edge" : gaps[nearest] <= 10 ? "near edge" : "INTERIOR"
      out.push(
        `  ${c.name.padEnd(8)} ${nearest.padEnd(6)} edge  ${mm(gaps[nearest]).padStart(7)} mm  ${verdict}` +
          `   (N ${mm(gaps.north)} S ${mm(gaps.south)} E ${mm(gaps.east)} W ${mm(gaps.west)})`,
      )
    }
    const sides = new Set(
      connectors.map((c) => {
        const g: Record<string, number> = {
          west: c.court.cx - c.court.w / 2 - (bx - halfW),
          east: bx + halfW - (c.court.cx + c.court.w / 2),
          south: c.court.cy - c.court.h / 2 - (by - halfH),
          north: by + halfH - (c.court.cy + c.court.h / 2),
        }
        return Object.keys(g).reduce((a, b) => (g[b] < g[a] ? b : a), "west")
      }),
    )
    if (connectors.length > 1 && sides.size === 1) {
      out.push(
        `  NOTE: every connector is on the ${[...sides][0]} edge. If the specification ` +
          `asked for them on different sides, this board does not do that.`,
      )
    }
  }

  // ── Crowding ─────────────────────────────────────────────────────────────────
  if (board && placed.length) {
    const lo = {
      x: Math.min(...placed.map((p) => p.court.cx - p.court.w / 2)),
      y: Math.min(...placed.map((p) => p.court.cy - p.court.h / 2)),
    }
    const hi = {
      x: Math.max(...placed.map((p) => p.court.cx + p.court.w / 2)),
      y: Math.max(...placed.map((p) => p.court.cy + p.court.h / 2)),
    }
    const used = (hi.x - lo.x) * (hi.y - lo.y)
    const courtArea = placed.reduce((s, p) => s + p.court.w * p.court.h, 0)
    out.push("")
    out.push(
      `OCCUPANCY  parts span ${mm(hi.x - lo.x)} x ${mm(hi.y - lo.y)} mm of an ` +
        `${mm(board.width)} x ${mm(board.height)} mm board ` +
        `(${((used / (board.width * board.height)) * 100).toFixed(0)}% of area). ` +
        `Courtyards cover ${((courtArea / (board.width * board.height)) * 100).toFixed(0)}% ` +
        `of the board.`,
    )
  }

  // ── Routing ──────────────────────────────────────────────────────────────────
  const traces = byType("pcb_trace")
  const vias = byType("pcb_via")
  const perLayer = new Map<string, number>()
  let totalLength = 0
  const detours: Array<{ net: string; length: number; direct: number; ratio: number }> = []

  for (const t of traces as any[]) {
    const route = (t.route ?? []).filter((r: any) => typeof r.x === "number")
    let length = 0
    for (let i = 1; i < route.length; i++) {
      const seg = dist(route[i - 1].x, route[i - 1].y, route[i].x, route[i].y)
      length += seg
      const layer = route[i].layer ?? "unknown"
      perLayer.set(layer, (perLayer.get(layer) ?? 0) + seg)
    }
    totalLength += length
    if (route.length >= 2) {
      const direct = dist(route[0].x, route[0].y, route[route.length - 1].x, route[route.length - 1].y)
      // A trace three times longer than the straight line between its endpoints is the
      // "looped across the board to reach a pad 2mm away" case the reviewer looks for.
      if (direct > 0.5 && length / direct >= 3) {
        detours.push({ net: t.connection_name ?? t.pcb_trace_id, length, direct, ratio: length / direct })
      }
    }
  }

  out.push("")
  out.push(
    `ROUTING  ${traces.length} trace(s), ${mm(totalLength)} mm total copper path, ` +
      `${vias.length} via(s).`,
  )
  if (perLayer.size) {
    const parts = [...perLayer.entries()]
      .filter(([, v]) => v > 0.005)
      .sort((a, b) => b[1] - a[1])
      .map(([l, v]) => `${l} ${mm(v)} mm`)
    out.push(`  per layer: ${parts.join(", ")}`)
  }
  detours.sort((a, b) => b.ratio - a.ratio)
  if (detours.length) {
    out.push(`  ${detours.length} trace(s) at least 3x longer than the direct path:`)
    for (const d of detours.slice(0, 10)) {
      out.push(
        `    ${d.net}: ${mm(d.length)} mm routed vs ${mm(d.direct)} mm direct (${d.ratio.toFixed(1)}x)`,
      )
    }
  } else {
    out.push("  no trace exceeds 3x its direct path length.")
  }

  // ── Silkscreen ───────────────────────────────────────────────────────────────
  const silk = byType("pcb_silkscreen_text")
  const silkOff = silk.filter((s: any) => {
    if (!board) return false
    const p = s.anchor_position ?? {}
    return (
      p.x < bx - halfW || p.x > bx + halfW || p.y < by - halfH || p.y > by + halfH
    )
  })
  const named = new Set(placed.map((p) => p.name))
  const silkTexts = new Set(silk.map((s: any) => String(s.text)))
  const missingRefs = [...named].filter((n) => !silkTexts.has(n))
  out.push("")
  out.push(
    `SILKSCREEN  ${silk.length} text item(s); ${silkOff.length} outside the board outline.`,
  )
  if (missingRefs.length) {
    out.push(
      `  ${missingRefs.length} component(s) with no silkscreen matching their reference ` +
        `designator: ${missingRefs.slice(0, 20).join(", ")}${missingRefs.length > 20 ? ", …" : ""}`,
    )
  }

  return out.join("\n")
}
