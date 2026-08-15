/**
 * Rule checks that need no PDE: geometric DRC against the board's own fabrication
 * limits, electrical rules over the netlist, and IPC-2221 trace-current capacity.
 *
 * These are cheap and exact, so they run every iteration and their output is treated
 * as ground truth by the review agents.
 */
import type { OperatingPoint } from "./model.ts"

export interface RuleViolation {
  rule: string
  severity: "error" | "warning"
  message: string
}

interface PadBox {
  net: string | undefined
  layer: string
  x: number
  y: number
  w: number
  h: number
  label: string
}

/** Half-extent overlap test with a clearance margin; returns the gap in mm. */
function gapBetween(a: PadBox, b: PadBox): number {
  const dx = Math.abs(a.x - b.x) - (a.w + b.w) / 2
  const dy = Math.abs(a.y - b.y) - (a.h + b.h) / 2
  if (dx >= 0 && dy >= 0) return Math.hypot(dx, dy)
  if (dx >= 0) return dx
  if (dy >= 0) return dy
  return Math.max(dx, dy) // both negative: overlapping
}

/** Net membership per pcb_port, derived the same way the netlist is. */
function netOfPort(circuitJson: any[]): Map<string, string> {
  const parent = new Map<string, string>()
  const find = (x: string): string => {
    if (!parent.has(x)) parent.set(x, x)
    if (parent.get(x) !== x) parent.set(x, find(parent.get(x)!))
    return parent.get(x)!
  }
  const union = (a: string, b: string) => {
    const [ra, rb] = [find(a), find(b)]
    if (ra !== rb) parent.set(ra, rb)
  }
  const netName = new Map<string, string>()
  for (const el of circuitJson) {
    if (el.type === "source_net") netName.set(el.source_net_id, el.name)
  }
  for (const el of circuitJson) {
    if (el.type !== "source_trace") continue
    const members = [...(el.connected_source_port_ids ?? []), ...(el.connected_source_net_ids ?? [])]
    for (let i = 1; i < members.length; i++) union(members[0], members[i])
  }
  const rootName = new Map<string, string>()
  for (const id of [...parent.keys()]) {
    const root = find(id)
    if (netName.has(id)) rootName.set(root, netName.get(id)!)
  }
  let anon = 0
  const nameOfRoot = (root: string) => {
    if (!rootName.has(root)) rootName.set(root, `N$${++anon}`)
    return rootName.get(root)!
  }

  const bySourcePort = new Map<string, string>()
  for (const id of [...parent.keys()]) bySourcePort.set(id, nameOfRoot(find(id)))

  const out = new Map<string, string>()
  for (const el of circuitJson) {
    if (el.type !== "pcb_port") continue
    const net = bySourcePort.get(el.source_port_id)
    if (net) out.set(el.pcb_port_id, net)
  }
  return out
}

export function checkGeometry(circuitJson: any[]): RuleViolation[] {
  const board = circuitJson.find((el) => el.type === "pcb_board")
  if (!board) return [{ rule: "board", severity: "error", message: "No pcb_board in the design." }]

  const violations: RuleViolation[] = []
  const netOf = netOfPort(circuitJson)
  const componentName = new Map<string, string>()
  const sourceById = new Map<string, any>()
  for (const el of circuitJson) {
    if (el.type === "source_component") sourceById.set(el.source_component_id, el)
  }
  for (const el of circuitJson) {
    if (el.type === "pcb_component") {
      componentName.set(el.pcb_component_id, sourceById.get(el.source_component_id)?.name ?? "?")
    }
  }

  const minPadClearance = board.min_pad_edge_to_pad_edge_clearance ?? 0.1
  const minTraceWidth = board.min_trace_width ?? 0.1
  const minViaHole = board.min_via_hole_diameter ?? 0.2
  const minViaPad = board.min_via_pad_diameter ?? 0.3
  const halfW = board.width / 2
  const halfH = board.height / 2

  const pads: PadBox[] = []
  for (const el of circuitJson) {
    if (el.type === "pcb_smtpad") {
      const w = el.shape === "circle" ? el.radius * 2 : el.width
      const h = el.shape === "circle" ? el.radius * 2 : el.height
      pads.push({
        net: netOf.get(el.pcb_port_id),
        layer: el.layer,
        x: el.x,
        y: el.y,
        w,
        h,
        label: `${componentName.get(el.pcb_component_id) ?? "?"}.${el.port_hints?.[0] ?? "?"}`,
      })
    }
    if (el.type === "pcb_plated_hole") {
      const w = el.rect_pad_width ?? el.outer_diameter ?? el.hole_diameter
      pads.push({
        net: netOf.get(el.pcb_port_id),
        layer: "top",
        x: el.x,
        y: el.y,
        w,
        h: el.rect_pad_height ?? w,
        label: `${componentName.get(el.pcb_component_id) ?? "?"}.${el.port_hints?.[0] ?? "?"}`,
      })
      const annular = ((el.rect_pad_width ?? el.outer_diameter ?? 0) - el.hole_diameter) / 2
      if (annular < 0.15) {
        violations.push({
          rule: "annular-ring",
          severity: "error",
          message: `Plated hole on ${componentName.get(el.pcb_component_id)} has ${annular.toFixed(3)}mm annular ring; fabs typically require 0.15mm.`,
        })
      }
    }
  }

  // Pad-to-pad clearance between different nets.
  for (let a = 0; a < pads.length; a++) {
    for (let b = a + 1; b < pads.length; b++) {
      if (pads[a].layer !== pads[b].layer) continue
      if (pads[a].net && pads[a].net === pads[b].net) continue
      const gap = gapBetween(pads[a], pads[b])
      if (gap < minPadClearance) {
        violations.push({
          rule: "pad-clearance",
          severity: gap < 0 ? "error" : "warning",
          message:
            gap < 0
              ? `Pads ${pads[a].label} and ${pads[b].label} overlap (${(-gap).toFixed(3)}mm) on different nets — this is a short.`
              : `Pads ${pads[a].label} and ${pads[b].label} are ${gap.toFixed(3)}mm apart, below the ${minPadClearance}mm minimum.`,
        })
      }
    }
  }

  // Component bodies: overlap and board-edge containment.
  const bodies = circuitJson.filter((el) => el.type === "pcb_component")
  for (let a = 0; a < bodies.length; a++) {
    const A = bodies[a]
    const nameA = componentName.get(A.pcb_component_id)
    if (
      Math.abs(A.center.x) + A.width / 2 > halfW ||
      Math.abs(A.center.y) + A.height / 2 > halfH
    ) {
      violations.push({
        rule: "board-outline",
        severity: "error",
        message: `${nameA} extends past the board outline (body ${A.width}x${A.height}mm at ${A.center.x},${A.center.y} on a ${board.width}x${board.height}mm board).`,
      })
    }
    for (let b = a + 1; b < bodies.length; b++) {
      const B = bodies[b]
      if (A.layer !== B.layer) continue
      const dx = Math.abs(A.center.x - B.center.x) - (A.width + B.width) / 2
      const dy = Math.abs(A.center.y - B.center.y) - (A.height + B.height) / 2
      if (dx < 0 && dy < 0) {
        violations.push({
          rule: "courtyard-overlap",
          severity: "error",
          message: `${nameA} and ${componentName.get(B.pcb_component_id)} overlap by ${Math.min(-dx, -dy).toFixed(2)}mm — they cannot both be assembled.`,
        })
      }
    }
  }

  // Routed copper against the fab limits.
  for (const el of circuitJson) {
    if (el.type === "pcb_trace") {
      for (const seg of el.route ?? []) {
        if (seg.route_type === "wire" && seg.width < minTraceWidth) {
          violations.push({
            rule: "trace-width",
            severity: "error",
            message: `Trace on ${el.connection_name} is ${seg.width}mm wide, below the ${minTraceWidth}mm minimum.`,
          })
          break
        }
      }
    }
    if (el.type === "pcb_via") {
      if (el.hole_diameter < minViaHole) {
        violations.push({
          rule: "via-hole",
          severity: "error",
          message: `Via at ${el.x},${el.y} has a ${el.hole_diameter}mm hole, below the ${minViaHole}mm minimum.`,
        })
      }
      if (el.outer_diameter < minViaPad) {
        violations.push({
          rule: "via-pad",
          severity: "error",
          message: `Via at ${el.x},${el.y} has a ${el.outer_diameter}mm pad, below the ${minViaPad}mm minimum.`,
        })
      }
    }
  }

  // Collapse repeats — one line per rule per pair is enough for the reviewer.
  const seen = new Set<string>()
  return violations.filter((v) => {
    const key = `${v.rule}|${v.message}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/** Electrical rules over the netlist plus the operating point. */
export function checkElectrical(circuitJson: any[], op: OperatingPoint): RuleViolation[] {
  const violations: RuleViolation[] = []
  const netOf = netOfPort(circuitJson)
  const nets = new Set(netOf.values())

  for (const rail of op.rails) {
    if (!nets.has(rail.net)) {
      violations.push({
        rule: "rail-missing",
        severity: "error",
        message: `Rail ${rail.net} from the operating point does not exist in the netlist.`,
      })
    }
  }

  // Every pin on a net.
  const componentById = new Map<string, any>()
  for (const el of circuitJson) {
    if (el.type === "source_component") componentById.set(el.source_component_id, el)
  }
  const connected = new Set<string>()
  for (const el of circuitJson) {
    if (el.type !== "source_trace") continue
    for (const p of el.connected_source_port_ids ?? []) connected.add(p)
  }
  const floating: string[] = []
  for (const el of circuitJson) {
    if (el.type !== "source_port") continue
    if (connected.has(el.source_port_id)) continue
    const comp = componentById.get(el.source_component_id)
    floating.push(`${comp?.name ?? "?"}.${el.name}`)
  }
  if (floating.length) {
    violations.push({
      rule: "floating-pin",
      severity: "warning",
      message: `Pins on no net: ${floating.join(", ")}. Connect them or state why they are deliberately open.`,
    })
  }

  // Decoupling proximity: for each pin declared as a load on a rail, is there a
  // capacitor pad on that same rail within 3mm?
  const capPorts: Array<{ x: number; y: number; net: string | undefined }> = []
  for (const el of circuitJson) {
    if (el.type !== "pcb_smtpad" && el.type !== "pcb_plated_hole") continue
    const comp = circuitJson.find(
      (c) => c.type === "pcb_component" && c.pcb_component_id === el.pcb_component_id,
    )
    const src = comp && componentById.get(comp.source_component_id)
    if (src?.ftype !== "simple_capacitor") continue
    capPorts.push({ x: el.x, y: el.y, net: netOf.get(el.pcb_port_id) })
  }

  const portPos = new Map<string, { x: number; y: number }>()
  for (const el of circuitJson) {
    if (el.type !== "pcb_port") continue
    const src = circuitJson.find(
      (p) => p.type === "source_port" && p.source_port_id === el.source_port_id,
    )
    const comp = src && componentById.get(src.source_component_id)
    if (!comp) continue
    portPos.set(`${comp.name}.${src.name}`.toLowerCase(), { x: el.x, y: el.y })
    for (const hint of src.port_hints ?? []) {
      portPos.set(`${comp.name}.${hint}`.toLowerCase(), { x: el.x, y: el.y })
    }
  }

  // Only integrated circuits need local decoupling; a resistor or an LED drawing from
  // a rail does not, and flagging those buries the finding that matters. One report
  // per chip, taking its hungriest pin.
  const chipNames = new Set(
    [...componentById.values()]
      .filter((c) => typeof c.ftype === "string" && c.ftype.includes("chip"))
      .map((c) => c.name),
  )
  const worstLoadPerChip = new Map<string, { pin: string; current_a: number }>()
  for (const load of op.loads) {
    const component = load.pin.split(".")[0]
    if (!chipNames.has(component)) continue
    const current = worstLoadPerChip.get(component)
    if (!current || load.current_a > current.current_a) {
      worstLoadPerChip.set(component, { pin: load.pin, current_a: load.current_a })
    }
  }

  for (const [component, load] of worstLoadPerChip) {
    const pos = portPos.get(load.pin.trim().toLowerCase())
    if (!pos) continue
    const net = op.loads.find((l) => l.pin === load.pin)?.net
    const near = capPorts.filter(
      (c) => c.net === net && Math.hypot(c.x - pos.x, c.y - pos.y) <= 3,
    )
    if (near.length) continue

    const anyOnNet = capPorts.filter((c) => c.net === net)
    violations.push({
      rule: "decoupling",
      severity: anyOnNet.length ? "warning" : "error",
      message: anyOnNet.length
        ? `${component} draws ${(load.current_a * 1000).toFixed(1)}mA at ${load.pin}, but the nearest capacitor on ${net} is ${Math.min(
            ...anyOnNet.map((c) => Math.hypot(c.x - pos.x, c.y - pos.y)),
          ).toFixed(1)}mm away. Move a 100nF cap within 2mm of the pin.`
        : `${component} draws ${(load.current_a * 1000).toFixed(1)}mA at ${load.pin} and there is no decoupling capacitor on ${net} anywhere. Add a 100nF cap from ${net} to ground within 2mm of ${load.pin}.`,
    })
  }

  return violations
}

export interface TraceCurrentCheck {
  net: string
  current_a: number
  narrowest_mm: number
  temperature_rise_c: number
  required_width_mm: number
  ok: boolean
}

/**
 * IPC-2221 trace-current capacity: I = k · ΔT^0.44 · A^0.725, with A in mil² and
 * k = 0.048 for external layers. Inverted here to get the rise for the width actually
 * routed, and solved forward for the width a 20°C rise would need.
 */
export function checkTraceCurrents(
  circuitJson: any[],
  op: OperatingPoint,
  maxRiseC = 20,
): TraceCurrentCheck[] {
  const K_EXTERNAL = 0.048
  const T_COPPER_MIL = 1.378 // 1oz copper in mils
  const MM_PER_MIL = 0.0254

  const checks: TraceCurrentCheck[] = []
  for (const rail of op.rails) {
    const current = op.loads
      .filter((l) => l.net === rail.net)
      .reduce((s, l) => s + l.current_a, 0)
    if (current <= 0) continue

    const net = circuitJson.find((el) => el.type === "source_net" && el.name === rail.net)
    if (!net) continue

    let narrowest = Infinity
    for (const el of circuitJson) {
      if (el.type !== "pcb_trace" || el.connection_name !== net.source_net_id) continue
      for (const seg of el.route ?? []) {
        if (seg.route_type === "wire" && seg.width) narrowest = Math.min(narrowest, seg.width)
      }
    }
    if (!isFinite(narrowest)) continue

    const areaMil2 = (narrowest / MM_PER_MIL) * T_COPPER_MIL
    const rise = Math.pow(current / (K_EXTERNAL * Math.pow(areaMil2, 0.725)), 1 / 0.44)
    const requiredAreaMil2 = Math.pow(
      current / (K_EXTERNAL * Math.pow(maxRiseC, 0.44)),
      1 / 0.725,
    )
    const requiredWidth = (requiredAreaMil2 / T_COPPER_MIL) * MM_PER_MIL

    checks.push({
      net: rail.net,
      current_a: current,
      narrowest_mm: narrowest,
      temperature_rise_c: rise,
      required_width_mm: requiredWidth,
      ok: rise <= maxRiseC,
    })
  }
  return checks
}
