/**
 * DC power integrity: IR drop and current density on each supply rail.
 *
 * Same solver as the thermal analysis, different physics. The rail's copper is a
 * resistive sheet, the source pin is a Dirichlet boundary at the rail voltage, each
 * load pin extracts its current, and the solution is the potential everywhere. The
 * gradient of that potential gives current density, which is what actually fuses a
 * trace.
 */
import {
  type Grid,
  makeGrid,
  solve,
  cellOf,
  idx,
  renderHeatmap,
  reachable,
} from "./field.ts"
import { rasterizeCopper } from "./copper.ts"
import type { OperatingPoint } from "./model.ts"

const SIGMA_COPPER = 5.8e7 // S/m
const T_COPPER = 35e-6 // m, 1oz

export interface RailResult {
  net: string
  voltage_v: number
  total_current_a: number
  max_drop_mv: number
  budget_mv: number
  within_budget: boolean
  worst_load: { pin: string; drop_mv: number } | undefined
  peak_current_density_a_per_mm2: number
  /** Narrowest copper the rail current has to squeeze through. */
  min_effective_width_mm: number
  heatmapSvg: string
  /** Set when the rail could not be solved, e.g. the source pin has no copper. */
  note?: string
}

/** Map "R1.pin2" to the pcb_port position, via the source-side names. */
function pinLocator(circuitJson: any[]) {
  const componentById = new Map<string, any>()
  for (const el of circuitJson) {
    if (el.type === "source_component") componentById.set(el.source_component_id, el)
  }
  const portKey = new Map<string, string>() // source_port_id -> "R1.pin2"
  for (const el of circuitJson) {
    if (el.type !== "source_port") continue
    const comp = componentById.get(el.source_component_id)
    if (!comp) continue
    portKey.set(el.source_port_id, `${comp.name}.${el.name}`)
    for (const hint of el.port_hints ?? []) {
      portKey.set(`${el.source_port_id}:${hint}`, `${comp.name}.${hint}`)
    }
  }
  const positions = new Map<string, { x: number; y: number }>()
  for (const el of circuitJson) {
    if (el.type !== "pcb_port") continue
    const key = portKey.get(el.source_port_id)
    if (key) positions.set(key.toLowerCase(), { x: el.x, y: el.y })
    // Also index by every hint so "U1.VCC" resolves as readily as "U1.pin8".
    const src = circuitJson.find(
      (p) => p.type === "source_port" && p.source_port_id === el.source_port_id,
    )
    const comp = src && componentById.get(src.source_component_id)
    if (comp) {
      for (const hint of src.port_hints ?? []) {
        positions.set(`${comp.name}.${hint}`.toLowerCase(), { x: el.x, y: el.y })
      }
    }
  }
  return (pin: string) => positions.get(pin.trim().toLowerCase())
}

/** Circuit JSON names nets by id (`source_net_1`), so map display name -> connection. */
function connectionNameFor(circuitJson: any[], netName: string): string | undefined {
  const net = circuitJson.find((el) => el.type === "source_net" && el.name === netName)
  if (!net) return undefined
  // pcb_trace.connection_name is the source_net_id for net-attached routes.
  const trace = circuitJson.find(
    (el) => el.type === "pcb_trace" && el.connection_name === net.source_net_id,
  )
  return trace ? net.source_net_id : undefined
}

export function analyzePowerIntegrity(
  circuitJson: any[],
  op: OperatingPoint,
): RailResult[] {
  const board = circuitJson.find((el) => el.type === "pcb_board")
  if (!board) return []

  // Resolve finer than the thermal grid: rail copper is a network of 0.15mm traces,
  // and the solve is only meaningful if that network is resolved as connected.
  // Capped so the grid stays a few hundred thousand cells at most.
  let narrowest = Infinity
  for (const el of circuitJson) {
    if (el.type !== "pcb_trace") continue
    for (const seg of el.route ?? []) {
      if (seg.route_type === "wire" && seg.width) narrowest = Math.min(narrowest, seg.width)
    }
  }
  const areaMm2 = board.width * board.height
  const cellMm = Math.max(
    isFinite(narrowest) ? narrowest / 2 : 0.075,
    Math.sqrt(areaMm2 / 200_000),
  )

  const g: Grid = makeGrid(board, cellMm)
  const n = g.nx * g.ny
  const locate = pinLocator(circuitJson)
  const results: RailResult[] = []

  for (const rail of op.rails) {
    const connection = connectionNameFor(circuitJson, rail.net)
    const copper = rasterizeCopper(circuitJson, g, { connectionName: connection })

    const K = new Float64Array(n)
    let coppered = 0
    for (let k = 0; k < n; k++) {
      const cu = copper.byLayer.top[k] + copper.byLayer.bottom[k]
      K[k] = SIGMA_COPPER * T_COPPER * cu
      if (cu > 0) coppered++
    }

    const loads = op.loads.filter((l) => l.net === rail.net)
    const totalCurrent = loads.reduce((s, l) => s + l.current_a, 0)

    const sourcePos = locate(rail.source_pin)
    if (!coppered || !sourcePos) {
      results.push({
        net: rail.net,
        voltage_v: rail.voltage_v,
        total_current_a: totalCurrent,
        max_drop_mv: 0,
        budget_mv: rail.max_drop_mv,
        within_budget: true,
        worst_load: undefined,
        peak_current_density_a_per_mm2: 0,
        min_effective_width_mm: 0,
        heatmapSvg: "",
        note: !coppered
          ? `No routed copper found for net ${rail.net}; the rail is unrouted or the net name does not match the netlist.`
          : `Source pin ${rail.source_pin} not found on the board.`,
      })
      continue
    }

    /**
     * A pin's exact cell can land just off the painted copper, so snap to the nearest
     * cell that has some — otherwise the injection is into an insulator and the solve
     * has no solution.
     */
    const snapToCopper = (x: number, y: number): number | undefined => {
      const [i0, j0] = cellOf(g, x, y)
      const limit = Math.ceil(0.5 / (g.width / g.nx)) // search out to 0.5mm
      for (let radius = 0; radius <= limit; radius++) {
        for (let dj = -radius; dj <= radius; dj++) {
          for (let di = -radius; di <= radius; di++) {
            if (Math.max(Math.abs(di), Math.abs(dj)) !== radius) continue
            const i = i0 + di
            const j = j0 + dj
            if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue
            const k = idx(g, i, j)
            if (K[k] > 0) return k
          }
        }
      }
      return undefined
    }

    const sourceCell = snapToCopper(sourcePos.x, sourcePos.y)
    if (sourceCell === undefined) {
      results.push({
        net: rail.net,
        voltage_v: rail.voltage_v,
        total_current_a: totalCurrent,
        max_drop_mv: 0,
        budget_mv: rail.max_drop_mv,
        within_budget: true,
        worst_load: undefined,
        peak_current_density_a_per_mm2: 0,
        min_effective_width_mm: 0,
        heatmapSvg: "",
        note: `Source pin ${rail.source_pin} has no copper of net ${rail.net} near it — the rail is not routed to its source.`,
      })
      continue
    }

    // Anything the source cannot reach through copper is a separate island; injecting
    // current there makes the system singular, so it is reported instead of solved.
    const connected = reachable(g, K, [sourceCell])

    const source = new Float64Array(n)
    const placedLoads: Array<{ pin: string; k: number }> = []
    const islands: string[] = []
    for (const load of loads) {
      const pos = locate(load.pin)
      if (!pos) continue
      const k = snapToCopper(pos.x, pos.y)
      if (k === undefined || !connected[k]) {
        islands.push(load.pin)
        continue
      }
      source[k] -= load.current_a
      placedLoads.push({ pin: load.pin, k })
    }

    // Restrict the domain to the connected island so the solve is well posed.
    const Kc = new Float64Array(n)
    for (let k = 0; k < n; k++) Kc[k] = connected[k] ? K[k] : 0

    const dirichlet = new Map<number, number>([[sourceCell, rail.voltage_v]])
    const { u: v } = solve({ grid: g, conductance: Kc, source, dirichlet })
    K.set(Kc)

    const drop = new Float64Array(n)
    let maxDrop = 0
    for (let k = 0; k < n; k++) {
      if (K[k] <= 0) continue
      drop[k] = (rail.voltage_v - v[k]) * 1000
      maxDrop = Math.max(maxDrop, drop[k])
    }

    let worst: { pin: string; drop_mv: number } | undefined
    for (const { pin, k } of placedLoads) {
      const d = (rail.voltage_v - v[k]) * 1000
      if (!worst || d > worst.drop_mv) worst = { pin, drop_mv: d }
    }

    // Current density from the potential gradient. In the sheet model the linear
    // current density is K·|∇V| [A/m]; dividing by the copper thickness gives the
    // true density J = σ·|∇V| [A/m²], which is 1e-6 of that in A/mm².
    const cellX = g.h
    const cellY = (g.height / g.ny) * 1e-3

    // Current is injected and extracted at single cells, which is singular by
    // construction — the gradient right at a pin is a discretisation artefact, not a
    // physical density. Mask a small neighbourhood around every injection point.
    const excluded = new Uint8Array(n)
    const maskRadius = Math.ceil(0.4 / (g.width / g.nx))
    for (const k0 of [sourceCell, ...placedLoads.map((l) => l.k)]) {
      const i0 = k0 % g.nx
      const j0 = (k0 - i0) / g.nx
      for (let dj = -maskRadius; dj <= maskRadius; dj++) {
        for (let di = -maskRadius; di <= maskRadius; di++) {
          const i = i0 + di
          const j = j0 + dj
          if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue
          excluded[idx(g, i, j)] = 1
        }
      }
    }

    /**
     * Cells outside the domain hold zero, so a centred difference across a
     * copper/no-copper edge reads the full rail voltage as a gradient. Difference
     * only against neighbours that are themselves copper.
     */
    const directional = (k: number, back: number, fwd: number, step: number): number => {
      const hasBack = K[back] > 0
      const hasFwd = K[fwd] > 0
      if (hasBack && hasFwd) return (v[fwd] - v[back]) / (2 * step)
      if (hasFwd) return (v[fwd] - v[k]) / step
      if (hasBack) return (v[k] - v[back]) / step
      return 0
    }

    let peakDensityMm2 = 0
    for (let j = 1; j < g.ny - 1; j++) {
      for (let i = 1; i < g.nx - 1; i++) {
        const k = idx(g, i, j)
        if (K[k] <= 0 || excluded[k]) continue
        const gx = directional(k, k - 1, k + 1, cellX)
        const gy = directional(k, k - g.nx, k + g.nx, cellY)
        const j_A_per_mm2 = (SIGMA_COPPER * Math.hypot(gx, gy)) / 1e6
        peakDensityMm2 = Math.max(peakDensityMm2, j_A_per_mm2)
      }
    }

    // Back out the narrowest copper the rail current squeezes through:
    // width = I / (J_peak · t), with the copper thickness in mm.
    const tMm = T_COPPER * 1e3
    const minWidth = peakDensityMm2 > 0 ? totalCurrent / (peakDensityMm2 * tMm) : 0

    results.push({
      net: rail.net,
      voltage_v: rail.voltage_v,
      total_current_a: totalCurrent,
      max_drop_mv: maxDrop,
      budget_mv: rail.max_drop_mv,
      within_budget: maxDrop <= rail.max_drop_mv,
      worst_load: worst,
      peak_current_density_a_per_mm2: peakDensityMm2,
      min_effective_width_mm: minWidth,
      note: islands.length
        ? `Not reachable through ${rail.net} copper from ${rail.source_pin}: ${islands.join(", ")}. Those loads were excluded — the rail is broken or unrouted to them.`
        : undefined,
      heatmapSvg: renderHeatmap({
        grid: g,
        field: drop,
        mask: K,
        title: `IR drop on ${rail.net} (${rail.voltage_v}V, ${(totalCurrent * 1000).toFixed(1)}mA)`,
        unit: "mV",
        min: 0,
        labels: placedLoads.map(({ pin, k }) => ({
          x: g.minX + ((((k % g.nx) + 0.5) / g.nx) * g.width),
          y: g.minY + (((Math.floor(k / g.nx) + 0.5) / g.ny) * g.height),
          text: pin,
        })),
      }),
    })
  }

  return results
}
