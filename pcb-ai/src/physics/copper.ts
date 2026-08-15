/**
 * Turns the PCB geometry in Circuit JSON into copper coverage fields.
 *
 * Coverage is the fraction of a cell occupied by copper, clamped to 1 — that is what
 * scales the sheet conductance in both the thermal and the power-integrity solve.
 */
import { type Grid, paintRect, paintDisc, paintSegment } from "./field.ts"

export type Layer = "top" | "bottom"

export interface CopperFields {
  /** Coverage in [0,1] per layer. */
  byLayer: Record<Layer, Float64Array>
  /** Coverage of everything, both layers summed then clamped — the thermal footprint. */
  any: Float64Array
}

function clamp01(field: Float64Array) {
  for (let k = 0; k < field.length; k++) field[k] = Math.min(1, field[k])
}

/** Paint every copper feature, optionally restricted to one connection (net). */
export function rasterizeCopper(
  circuitJson: any[],
  g: Grid,
  opts: { connectionName?: string } = {},
): CopperFields {
  const n = g.nx * g.ny
  const byLayer: Record<Layer, Float64Array> = {
    top: new Float64Array(n),
    bottom: new Float64Array(n),
  }
  const cellArea = (g.width / g.nx) * (g.height / g.ny)

  /** A feature of area A in mm² contributes A/cellArea coverage to the cells it spans. */
  const only = opts.connectionName

  // Which pcb_ports belong to the requested net, so pads can be filtered too.
  let allowedPorts: Set<string> | undefined
  if (only) {
    allowedPorts = new Set()
    for (const el of circuitJson) {
      if (el.type === "pcb_trace" && el.connection_name === only) {
        for (const p of el.connectsTo ?? []) allowedPorts.add(p)
      }
    }
  }

  for (const el of circuitJson) {
    switch (el.type) {
      case "pcb_smtpad": {
        if (allowedPorts && !allowedPorts.has(el.pcb_port_id)) break
        const layer: Layer = el.layer === "bottom" ? "bottom" : "top"
        if (el.shape === "circle") {
          paintDisc(g, byLayer[layer], el.x, el.y, el.radius * 2, 1)
        } else {
          paintRect(g, byLayer[layer], el.x, el.y, el.width, el.height, 1)
        }
        break
      }
      case "pcb_plated_hole": {
        if (allowedPorts && !allowedPorts.has(el.pcb_port_id)) break
        const w = el.rect_pad_width ?? el.outer_diameter ?? el.hole_diameter * 2
        const h = el.rect_pad_height ?? w
        for (const layer of ["top", "bottom"] as Layer[]) {
          paintRect(g, byLayer[layer], el.x, el.y, w, h, 1)
        }
        break
      }
      case "pcb_via": {
        for (const layer of ["top", "bottom"] as Layer[]) {
          paintDisc(g, byLayer[layer], el.x, el.y, el.outer_diameter ?? 0.3, 1)
        }
        break
      }
      case "pcb_trace": {
        if (only && el.connection_name !== only) break
        const route = el.route ?? []
        for (let i = 1; i < route.length; i++) {
          const a = route[i - 1]
          const b = route[i]
          if (a.route_type !== "wire" || b.route_type !== "wire") continue
          if (a.layer !== b.layer) continue
          const layer: Layer = b.layer === "bottom" ? "bottom" : "top"
          const width = b.width ?? a.width ?? 0.15
          // Coverage is the fraction of the cell the trace actually fills, so a
          // 0.15mm trace on a 0.05mm grid reads as solid copper and on a coarse grid
          // reads as partial — which is what scales the sheet conductance correctly.
          const cellMm = g.width / g.nx
          paintSegment(g, byLayer[layer], a.x, a.y, b.x, b.y, width, Math.min(1, width / cellMm))
        }
        break
      }
      case "pcb_copper_pour": {
        if (only && el.connection_name !== only) break
        const layer: Layer = el.layer === "bottom" ? "bottom" : "top"
        if (el.shape === "rect") paintRect(g, byLayer[layer], el.center?.x ?? el.x, el.center?.y ?? el.y, el.width, el.height, 1)
        break
      }
    }
  }

  clamp01(byLayer.top)
  clamp01(byLayer.bottom)

  const any = new Float64Array(n)
  for (let k = 0; k < n; k++) any[k] = Math.min(1, byLayer.top[k] + byLayer.bottom[k])

  void cellArea
  return { byLayer, any }
}
