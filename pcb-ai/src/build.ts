/**
 * Deterministic half of the pipeline: compile the HDL, extract machine-checkable
 * findings, and render the views the AI reviewer will look at.
 *
 * Nothing here calls a model. Everything the reviewer sees is produced by this file.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { runTscircuitCode } from "@tscircuit/eval"
import {
  convertCircuitJsonToSchematicSvg,
  convertCircuitJsonToPcbSvg,
  convertCircuitJsonToAssemblySvg,
} from "circuit-to-svg"
import { Resvg } from "@resvg/resvg-js"
import type { BuildResult, CircuitFinding, NetSummary } from "./types.ts"

/** Views rendered every iteration and handed to the reviewer. */
const VIEWS = {
  schematic: convertCircuitJsonToSchematicSvg,
  pcb: convertCircuitJsonToPcbSvg,
  assembly: convertCircuitJsonToAssemblySvg,
} as const

/**
 * Warnings that say nothing about whether the board works. Left in the prompt they
 * crowd out the real findings — a 20-part design emits ~40 of them — so they are
 * counted but not itemised.
 */
const LOW_SIGNAL_WARNINGS = new Set([
  "source_unnamed_trace_warning",
  "supplier_footprint_mismatch_warning",
  "source_property_ignored_warning",
])

/** Circuit JSON marks problems with element types ending in these suffixes. */
function classify(type: string): "error" | "warning" | null {
  if (type.endsWith("_error")) return "error"
  if (type.endsWith("_warning")) return "warning"
  return null
}

function extractFindings(circuitJson: any[]): CircuitFinding[] {
  const findings: CircuitFinding[] = []
  for (const el of circuitJson) {
    const severity = classify(el.type)
    if (!severity) continue
    findings.push({
      type: el.type,
      severity,
      message: el.message ?? el.error_type ?? el.warning_type ?? JSON.stringify(el),
    })
  }
  return findings
}

export function isLowSignal(f: CircuitFinding): boolean {
  return f.severity === "warning" && LOW_SIGNAL_WARNINGS.has(f.type)
}

/**
 * Rebuild the netlist by unioning every source_trace's endpoints.
 *
 * `subcircuit_connectivity_map_key` already encodes this, but it is absent on
 * ports, so a small union-find over the traces is the reliable path.
 */
function extractNetlist(circuitJson: any[]): NetSummary[] {
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

  const componentById = new Map<string, any>()
  for (const el of circuitJson) {
    if (el.type === "source_component") componentById.set(el.source_component_id, el)
  }

  /** source_port_id -> "R1.pin2" */
  const portLabel = new Map<string, string>()
  for (const el of circuitJson) {
    if (el.type !== "source_port") continue
    const comp = componentById.get(el.source_component_id)
    portLabel.set(el.source_port_id, `${comp?.name ?? "?"}.${el.name}`)
  }

  const netName = new Map<string, string>()
  for (const el of circuitJson) {
    if (el.type === "source_net") netName.set(el.source_net_id, el.name)
  }

  for (const el of circuitJson) {
    if (el.type !== "source_trace") continue
    const members = [
      ...(el.connected_source_port_ids ?? []),
      ...(el.connected_source_net_ids ?? []),
    ]
    for (let i = 1; i < members.length; i++) union(members[0], members[i])
  }

  const groups = new Map<string, string[]>()
  for (const id of parent.keys()) {
    const root = find(id)
    if (!groups.has(root)) groups.set(root, [])
    groups.get(root)!.push(id)
  }

  const nets: NetSummary[] = []
  let anon = 0
  for (const members of groups.values()) {
    const named = members.find((m) => netName.has(m))
    const connections = members
      .filter((m) => portLabel.has(m))
      .map((m) => portLabel.get(m)!)
      .sort()
    nets.push({
      name: named ? netName.get(named)! : `N$${++anon}`,
      connections,
    })
  }
  return nets.sort((a, b) => a.name.localeCompare(b.name))
}

function extractComponents(circuitJson: any[]) {
  const footprintByComponent = new Map<string, string>()
  for (const el of circuitJson) {
    if (el.type === "pcb_component" && el.source_component_id) {
      // The footprint name is not always echoed back; fall back to the pad count.
      const pads = circuitJson.filter(
        (p) =>
          (p.type === "pcb_smtpad" || p.type === "pcb_plated_hole") &&
          p.pcb_component_id === el.pcb_component_id,
      ).length
      footprintByComponent.set(el.source_component_id, `${pads}-pad`)
    }
  }
  return circuitJson
    .filter((el) => el.type === "source_component")
    .map((el) => ({
      name: el.name,
      type: el.ftype ?? "unknown",
      footprint: footprintByComponent.get(el.source_component_id),
      value:
        el.display_resistance ??
        el.display_capacitance ??
        el.display_inductance ??
        el.manufacturer_part_number,
    }))
}

async function renderViews(circuitJson: any[], dir: string): Promise<Record<string, string>> {
  const images: Record<string, string> = {}
  for (const [name, convert] of Object.entries(VIEWS)) {
    try {
      const svg = convert(circuitJson as any)
      const svgPath = path.join(dir, `${name}.svg`)
      const pngPath = path.join(dir, `${name}.png`)
      await fs.writeFile(svgPath, svg)
      // 1400px wide keeps silkscreen text legible to the vision model without
      // blowing past the per-image token budget.
      const png = new Resvg(svg, {
        fitTo: { mode: "width", value: 1400 },
        background: "white",
      })
        .render()
        .asPng()
      await fs.writeFile(pngPath, png)
      images[name] = pngPath
    } catch (err) {
      // A view that fails to render is not fatal — the reviewer just sees fewer images.
      console.warn(`  ! could not render ${name} view: ${(err as Error).message}`)
    }
  }
  return images
}

/** Compile one revision of the HDL and render everything the reviewer needs. */
export async function build(code: string, dir: string): Promise<BuildResult> {
  await fs.mkdir(dir, { recursive: true })
  await fs.writeFile(path.join(dir, "circuit.tsx"), code)

  let circuitJson: any[]
  try {
    circuitJson = await runTscircuitCode(code)
  } catch (err) {
    return {
      ok: false,
      compileError: (err as Error).stack ?? String(err),
      circuitJson: [],
      findings: [],
      netlist: [],
      components: [],
      images: {},
    }
  }

  await fs.writeFile(path.join(dir, "circuit.json"), JSON.stringify(circuitJson, null, 2))

  const findings = extractFindings(circuitJson)
  const board = circuitJson.find((el) => el.type === "pcb_board")

  return {
    ok: !findings.some((f) => f.severity === "error"),
    circuitJson,
    findings,
    netlist: extractNetlist(circuitJson),
    components: extractComponents(circuitJson),
    board: board
      ? { width: board.width, height: board.height, layerCount: board.num_layers }
      : undefined,
    images: await renderViews(circuitJson, dir),
  }
}

/** Compact text digest of a build — this is what goes into the reviewer's prompt. */
export function describeBuild(b: BuildResult): string {
  if (b.compileError) return `HDL FAILED TO COMPILE:\n${b.compileError}`

  const lines: string[] = []
  if (b.board) {
    lines.push(
      `Board: ${b.board.width}mm x ${b.board.height}mm, ${b.board.layerCount} layer(s)`,
    )
  } else {
    lines.push("Board: NONE — no <board> element produced a pcb_board.")
  }

  lines.push("", `Components (${b.components.length}):`)
  for (const c of b.components) {
    lines.push(
      `  ${c.name}  ${c.type}${c.value ? `  ${c.value}` : ""}${c.footprint ? `  [${c.footprint}]` : ""}`,
    )
  }

  lines.push("", `Nets (${b.netlist.length}):`)
  for (const n of b.netlist) {
    lines.push(`  ${n.name}: ${n.connections.join(", ") || "(no pins)"}`)
  }

  const errors = b.findings.filter((f) => f.severity === "error")
  const warnings = b.findings.filter((f) => f.severity === "warning" && !isLowSignal(f))
  const suppressed = b.findings.filter(isLowSignal)

  // When autorouting is skipped, every net on the board reports as unconnected. Those
  // are consequences of one root cause, and a reviewer that treats them as independent
  // faults will spend the whole iteration adding traces that already exist.
  const routingAborted = errors.find((f) => f.type === "pcb_autorouting_error")
  const cascade = routingAborted
    ? errors.filter(
        (f) =>
          f.type === "pcb_port_not_connected_error" || f.type === "pcb_trace_missing_error",
      )
    : []

  lines.push("", `Compiler errors (${errors.length}):`)
  if (routingAborted) {
    lines.push(
      `  ROOT CAUSE — [${routingAborted.type}] ${routingAborted.message}`,
      `  Autorouting did not run, so no copper was placed. The ${cascade.length} ` +
        `"not connected" / "no PCB trace" errors below are all consequences of this ` +
        `one failure, not separate faults. Fix the cause; do not add traces.`,
      "",
    )
  }
  for (const f of errors) {
    if (f === routingAborted) continue
    const tag = cascade.includes(f) ? "  (consequence) " : "  "
    lines.push(`${tag}[${f.type}] ${f.message}`)
  }
  lines.push("", `Compiler warnings (${warnings.length}):`)
  for (const f of warnings) lines.push(`  [${f.type}] ${f.message}`)
  if (suppressed.length) {
    const byType = new Map<string, number>()
    for (const f of suppressed) byType.set(f.type, (byType.get(f.type) ?? 0) + 1)
    lines.push(
      "",
      `Cosmetic warnings, not itemised: ${[...byType]
        .map(([t, n]) => `${t} x${n}`)
        .join(", ")}`,
    )
  }

  return lines.join("\n")
}
