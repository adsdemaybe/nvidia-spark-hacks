/** Runs every analysis and produces the physics report the agents review. */
import fs from "node:fs/promises"
import path from "node:path"
import { Resvg } from "@resvg/resvg-js"
import { analyzeThermal, type ThermalResult } from "./thermal.ts"
import { analyzePowerIntegrity, type RailResult } from "./power.ts"
import {
  checkGeometry,
  checkElectrical,
  checkTraceCurrents,
  type RuleViolation,
  type TraceCurrentCheck,
} from "./rules.ts"
import type { OperatingPoint } from "./model.ts"

export * from "./model.ts"

export interface PhysicsReport {
  operatingPoint: OperatingPoint
  thermal?: ThermalResult
  rails: RailResult[]
  traceCurrents: TraceCurrentCheck[]
  geometry: RuleViolation[]
  electrical: RuleViolation[]
  /** Rendered field images, keyed by view name. */
  images: Record<string, string>
}

async function writeSvgAndPng(svg: string, file: string) {
  await fs.writeFile(`${file}.svg`, svg)
  const png = new Resvg(svg, { fitTo: { mode: "width", value: 1400 } }).render().asPng()
  await fs.writeFile(`${file}.png`, png)
  return `${file}.png`
}

export async function runPhysics(
  circuitJson: any[],
  op: OperatingPoint,
  dir: string,
): Promise<PhysicsReport> {
  await fs.mkdir(dir, { recursive: true })

  const thermal = analyzeThermal(circuitJson, op)
  const rails = analyzePowerIntegrity(circuitJson, op)
  const images: Record<string, string> = {}

  if (thermal?.heatmapSvg) {
    images.thermal = await writeSvgAndPng(thermal.heatmapSvg, path.join(dir, "thermal"))
  }
  for (const rail of rails) {
    if (!rail.heatmapSvg) continue
    const safe = rail.net.replace(/[^\w.-]/g, "_")
    images[`ir-drop-${rail.net}`] = await writeSvgAndPng(
      rail.heatmapSvg,
      path.join(dir, `ir-drop-${safe}`),
    )
  }

  return {
    operatingPoint: op,
    thermal,
    rails,
    traceCurrents: checkTraceCurrents(circuitJson, op),
    geometry: checkGeometry(circuitJson),
    electrical: checkElectrical(circuitJson, op),
    images,
  }
}

/** Text digest of the physics report, for the review prompts and for `report.txt`. */
export function describePhysics(r: PhysicsReport): string {
  const out: string[] = []
  const op = r.operatingPoint

  out.push("OPERATING POINT (modelled, not measured)")
  out.push(`  ambient: ${op.ambient_c}°C`)
  for (const rail of op.rails) {
    out.push(
      `  rail ${rail.net}: ${rail.voltage_v}V from ${rail.source_pin}, budget ${rail.max_drop_mv}mV`,
    )
  }
  for (const a of op.assumptions) out.push(`  assumption: ${a}`)

  if (r.thermal) {
    const t = r.thermal
    out.push(
      "",
      `THERMAL (2D steady-state, natural convection, first-order)`,
      `  total dissipation: ${t.total_power_w.toFixed(3)} W`,
      `  peak: ${t.peak_c.toFixed(1)}°C (${t.peak_rise_c.toFixed(1)}°C above ambient)`,
      `  solver: ${t.iterations} iterations, residual ${t.residual.toExponential(1)}`,
    )
    for (const c of t.components) {
      if (c.power_w <= 0 && c.margin_c > 30) continue
      out.push(
        `  ${c.component}: ${c.temperature_c.toFixed(1)}°C at ${c.power_w.toFixed(3)}W` +
          `  (max ${c.max_temp_c}°C, margin ${c.margin_c.toFixed(1)}°C)` +
          (c.margin_c < 0 ? "  ** OVER TEMPERATURE **" : ""),
      )
    }
  } else {
    out.push("", "THERMAL: not run (no board geometry)")
  }

  out.push("", "POWER INTEGRITY (DC IR drop)")
  if (!r.rails.length) out.push("  no rails modelled")
  for (const rail of r.rails) {
    if (rail.note) {
      out.push(`  ${rail.net}: ${rail.note}`)
      continue
    }
    out.push(
      `  ${rail.net}: ${(rail.total_current_a * 1000).toFixed(1)}mA, max drop ` +
        `${rail.max_drop_mv.toFixed(1)}mV against a ${rail.budget_mv}mV budget` +
        (rail.within_budget ? "" : "  ** OVER BUDGET **"),
    )
    if (rail.worst_load) {
      out.push(
        `    worst load ${rail.worst_load.pin} at ${rail.worst_load.drop_mv.toFixed(1)}mV`,
      )
    }
    out.push(
      `    peak current density ${rail.peak_current_density_a_per_mm2.toFixed(1)} A/mm²,` +
        ` narrowest effective copper ${rail.min_effective_width_mm.toFixed(3)}mm`,
    )
  }

  out.push("", "TRACE CURRENT (IPC-2221, 1oz external)")
  if (!r.traceCurrents.length) out.push("  nothing to check")
  for (const c of r.traceCurrents) {
    out.push(
      `  ${c.net}: ${(c.current_a * 1000).toFixed(1)}mA through ${c.narrowest_mm}mm ` +
        `→ ${c.temperature_rise_c.toFixed(1)}°C rise` +
        (c.ok ? "" : `  ** needs ${c.required_width_mm.toFixed(2)}mm **`),
    )
  }

  const geoErrors = r.geometry.filter((v) => v.severity === "error")
  const geoWarn = r.geometry.filter((v) => v.severity === "warning")
  out.push("", `GEOMETRIC DRC (${geoErrors.length} errors, ${geoWarn.length} warnings)`)
  for (const v of [...geoErrors, ...geoWarn]) out.push(`  [${v.rule}] ${v.message}`)

  const elecErrors = r.electrical.filter((v) => v.severity === "error")
  const elecWarn = r.electrical.filter((v) => v.severity === "warning")
  out.push("", `ELECTRICAL RULES (${elecErrors.length} errors, ${elecWarn.length} warnings)`)
  for (const v of [...elecErrors, ...elecWarn]) out.push(`  [${v.rule}] ${v.message}`)

  return out.join("\n")
}

/** Hard physics failures — used to force another iteration regardless of opinion. */
export function physicsBlockers(r: PhysicsReport): string[] {
  const blockers: string[] = []
  for (const v of r.geometry) if (v.severity === "error") blockers.push(`DRC: ${v.message}`)
  for (const v of r.electrical) if (v.severity === "error") blockers.push(`ERC: ${v.message}`)
  for (const c of r.thermal?.components ?? []) {
    if (c.margin_c < 0) {
      blockers.push(
        `Thermal: ${c.component} reaches ${c.temperature_c.toFixed(1)}°C, over its ${c.max_temp_c}°C limit.`,
      )
    }
  }
  for (const rail of r.rails) {
    if (!rail.within_budget) {
      blockers.push(
        `IR drop: ${rail.net} drops ${rail.max_drop_mv.toFixed(1)}mV, over its ${rail.budget_mv}mV budget.`,
      )
    }
  }
  for (const c of r.traceCurrents) {
    if (!c.ok) {
      blockers.push(
        `Trace current: ${c.net} rises ${c.temperature_rise_c.toFixed(1)}°C; widen to ${c.required_width_mm.toFixed(2)}mm.`,
      )
    }
  }
  return blockers
}
