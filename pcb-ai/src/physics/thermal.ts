/**
 * Steady-state thermal analysis.
 *
 * A 2D lumped-plane model of the board: copper and FR4 conduct in-plane, both faces
 * lose heat to still air by convection, and each component injects its dissipation
 * over its own footprint. This is the standard first-order model for a small
 * naturally-cooled board — it will not resolve a via farm or forced airflow, and it is
 * labelled as first-order everywhere it is reported.
 */
import {
  type Grid,
  makeGrid,
  solve,
  paintRect,
  averageOver,
  renderHeatmap,
  idx,
} from "./field.ts"
import { rasterizeCopper } from "./copper.ts"
import type { OperatingPoint } from "./model.ts"

/** Material constants, SI. */
const K_COPPER = 385 // W/m·K
const K_FR4 = 0.3 // W/m·K, through-plane and in-plane are close enough at this fidelity
const T_COPPER = 35e-6 // m, 1oz
const H_CONVECTION = 10 // W/m²·K per face, natural convection to still air

export interface ComponentTemperature {
  component: string
  power_w: number
  temperature_c: number
  max_temp_c: number
  margin_c: number
}

export interface ThermalResult {
  ambient_c: number
  peak_c: number
  peak_rise_c: number
  total_power_w: number
  components: ComponentTemperature[]
  /** Path of the rendered heatmap SVG. */
  heatmapSvg: string
  iterations: number
  residual: number
}

export function analyzeThermal(
  circuitJson: any[],
  op: OperatingPoint,
): ThermalResult | undefined {
  const board = circuitJson.find((el) => el.type === "pcb_board")
  if (!board) return undefined

  const g: Grid = makeGrid(board)
  const n = g.nx * g.ny
  const copper = rasterizeCopper(circuitJson, g)

  const boardThickness = (board.thickness ?? 1.6) * 1e-3

  // Sheet conductance per cell: copper on both layers in parallel with the FR4 core.
  const K = new Float64Array(n)
  for (let k = 0; k < n; k++) {
    const cu = copper.byLayer.top[k] + copper.byLayer.bottom[k]
    K[k] = K_COPPER * T_COPPER * cu + K_FR4 * boardThickness
  }

  // Convection off both faces of each cell, as an absorption term. Solving for rise
  // above ambient keeps the equation homogeneous in the boundary condition.
  const cellArea = g.h * ((g.height / g.ny) * 1e-3)
  const absorption = new Float64Array(n)
  for (let k = 0; k < n; k++) absorption[k] = 2 * H_CONVECTION * cellArea

  // Inject each component's dissipation over its own body.
  const source = new Float64Array(n)
  const powerOf = new Map(op.dissipation.map((d) => [d.component, d]))
  const sourceByComponent = new Map<string, any>()
  for (const el of circuitJson) {
    if (el.type === "source_component") sourceByComponent.set(el.source_component_id, el)
  }

  const placed: Array<{ name: string; el: any; power: number }> = []
  let totalPower = 0
  for (const el of circuitJson) {
    if (el.type !== "pcb_component") continue
    const src = sourceByComponent.get(el.source_component_id)
    const model = src && powerOf.get(src.name)
    if (!src) continue
    const power = model?.power_w ?? 0
    placed.push({ name: src.name, el, power })
    if (power <= 0) continue
    totalPower += power
    // paintRect accumulates, so divide by the cell count the body spans.
    const cells = Math.max(
      1,
      Math.ceil((el.width / g.width) * g.nx) * Math.ceil((el.height / g.height) * g.ny),
    )
    paintRect(g, source, el.center.x, el.center.y, el.width, el.height, power / cells)
  }

  const inDomain = new Float64Array(n).fill(1) // the whole board conducts, copper or not
  const { u: rise, iterations, residual } = solve({
    grid: g,
    conductance: K,
    source,
    absorption,
  })

  let peakRise = 0
  for (let k = 0; k < n; k++) peakRise = Math.max(peakRise, rise[k])

  const components: ComponentTemperature[] = placed.map(({ name, el, power }) => {
    const local =
      averageOver(g, rise, inDomain, el.center.x, el.center.y, el.width, el.height) ?? 0
    const model = powerOf.get(name)
    const temperature = op.ambient_c + local
    const max = model?.max_temp_c ?? 85
    return {
      component: name,
      power_w: power,
      temperature_c: temperature,
      max_temp_c: max,
      margin_c: max - temperature,
    }
  })

  const labels = placed.map(({ name, el }) => ({
    x: el.center.x,
    y: el.center.y,
    text: name,
  }))

  const absolute = new Float64Array(n)
  for (let k = 0; k < n; k++) absolute[k] = op.ambient_c + rise[k]

  void idx
  return {
    ambient_c: op.ambient_c,
    peak_c: op.ambient_c + peakRise,
    peak_rise_c: peakRise,
    total_power_w: totalPower,
    components: components.sort((a, b) => b.temperature_c - a.temperature_c),
    heatmapSvg: renderHeatmap({
      grid: g,
      field: absolute,
      mask: inDomain,
      title: "Steady-state temperature",
      unit: "°C",
      labels,
    }),
    iterations,
    residual,
  }
}
