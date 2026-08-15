/**
 * Derive a §6 `board_report` from tscircuit Circuit JSON.
 *
 * ## The honest part
 *
 * Circuit JSON has no Z axis. `pcb_component.width`/`.height` are the footprint's
 * **X and Y extents on the board plane** — there is no field anywhere in the
 * format that says how tall a part stands. But an enclosure is exactly the thing
 * that needs to know.
 *
 * So component heights come from a footprint table below, and **every one of them
 * is an assumption**. Rather than bury that, `deriveBoardReport` returns the
 * assumptions alongside the report, each labelled with the provenance status the
 * platform already uses (§1.1: "every physical constant carries provenance —
 * CONFIRMED | INFERRED | ASSUMED | MEASURED"). A caller that wants real numbers
 * supplies `heightOverrides` from a part datasheet, and those come back CONFIRMED.
 *
 * The failure this avoids is specific and expensive: an enclosure that passes
 * every deterministic gate, gets ordered, and then doesn't close because the
 * electrolytic nobody measured is 4mm taller than the table guessed.
 */

import type {
  BoardMass,
  BoardReport,
  ComponentHeight,
  ConnectorEdge,
  Edge,
  MountingHole,
  ThermalHotspot,
} from "./contracts.ts"

export type ProvenanceStatus = "CONFIRMED" | "INFERRED" | "ASSUMED" | "MEASURED"

export interface Assumption {
  subject: string
  field: string
  value: number
  unit: string
  status: ProvenanceStatus
  source: string
  note: string
}

export interface DerivedBoardReport {
  boardReport: BoardReport
  assumptions: Assumption[]
}

export interface DeriveOptions {
  designId?: string
  /** Known component heights in mm, keyed by designator (e.g. `{ C1: 10.5 }`).
   *  These are treated as CONFIRMED and override the footprint table. */
  heightOverrides?: Record<string, number>
  /** Power dissipation in W, keyed by designator. Circuit JSON does not carry
   *  power; pass pcb-ai's physics report in here or leave empty. */
  powerByRef?: Record<string, number>
  /** Designators to treat as edge connectors needing a cutout. Auto-detected
   *  when omitted. */
  connectorRefs?: string[]
  /** Fallback height for a footprint the table doesn't know. */
  defaultHeightMm?: number
  /** Known populated masses in grams, keyed by designator. CONFIRMED, and they
   *  override the footprint mass table. Supply these from the BOM when it
   *  carries per-line masses — the table is a stand-in for exactly this. */
  massOverrides?: Record<string, number>
}

/**
 * Typical body heights in mm, by footprint substring. Values are ordinary
 * package maxima, not any specific vendor's part — which is exactly why they
 * are ASSUMED and not CONFIRMED.
 */
const FOOTPRINT_HEIGHT_MM: Array<[RegExp, number]> = [
  [/^0201/i, 0.3],
  [/^0402/i, 0.55],
  [/^0603/i, 0.8],
  [/^0805/i, 0.95],
  [/^1206/i, 1.1],
  [/^1210/i, 1.4],
  [/sot-?23/i, 1.2],
  [/sot-?223/i, 1.8],
  [/sod-?123/i, 1.35],
  [/qfn/i, 1.0],
  [/dfn/i, 0.8],
  [/soic/i, 1.75],
  [/ssop|tssop/i, 1.2],
  [/[tl]qfp/i, 1.6],
  [/bga/i, 1.5],
  [/dip(_|-)?\d*/i, 4.5],
  [/usb.?c/i, 3.2],
  [/usb/i, 4.5],
  [/pinrow|header|hdr/i, 8.5],
  [/terminal|screw/i, 10.0],
  [/electrolytic|cap_?radial|radial/i, 10.0],
  [/tantalum/i, 2.8],
  [/crystal|xtal|hc49/i, 3.0],
  [/inductor|choke/i, 3.0],
  [/led/i, 0.8],
  [/switch|button|tact/i, 3.5],
  [/relay/i, 15.0],
  [/screwterminal/i, 10.0],
]

const DEFAULT_HEIGHT_MM = 2.0

/**
 * Density of FR-4 laminate, g/cm^3. A material constant, not a board property:
 * the substrate mass is area x thickness x this, and every term but this one
 * comes off the routed artifact.
 */
const FR4_DENSITY_G_PER_CM3 = 1.85

/**
 * Typical populated masses in grams, by footprint substring — the mass twin of
 * `FOOTPRINT_HEIGHT_MM`, and ASSUMED for exactly the same reason. Circuit JSON
 * has no mass field any more than it has a Z axis.
 *
 * These matter less than the heights do, and it is worth saying why rather than
 * treating them as equally load-bearing: a 0.5 g error on a passive is invisible
 * next to a 12 g substrate, whereas a 4 mm height error on an electrolytic is an
 * enclosure that does not close. The figures that do move the total are the
 * connectors and the electrolytics, which is where the table is most specific.
 */
const FOOTPRINT_MASS_G: Array<[RegExp, number]> = [
  [/^0201/i, 0.0002],
  [/^0402/i, 0.0006],
  [/^0603/i, 0.002],
  [/^0805/i, 0.004],
  [/^1206/i, 0.01],
  [/^1210/i, 0.015],
  [/sot-?23/i, 0.008],
  [/sot-?223/i, 0.06],
  [/sod-?123/i, 0.01],
  [/qfn/i, 0.05],
  [/dfn/i, 0.02],
  [/soic/i, 0.15],
  [/ssop|tssop/i, 0.06],
  [/[tl]qfp/i, 0.35],
  [/bga/i, 0.4],
  [/dip(_|-)?\d*/i, 1.0],
  [/usb.?c/i, 1.0],
  [/usb/i, 1.8],
  [/pinrow|header|hdr/i, 0.9],
  [/terminal|screw/i, 2.4],
  [/electrolytic|cap_?radial|radial/i, 2.0],
  [/tantalum/i, 0.1],
  [/crystal|xtal|hc49/i, 0.5],
  [/inductor|choke/i, 0.8],
  [/led/i, 0.005],
  [/switch|button|tact/i, 0.35],
  [/relay/i, 10.0],
  [/screwterminal/i, 2.4],
]

const DEFAULT_MASS_G = 0.1

function lookupMass(footprint: string | undefined): { g: number; matched: string | null } {
  if (!footprint) return { g: DEFAULT_MASS_G, matched: null }
  for (const [re, g] of FOOTPRINT_MASS_G) {
    if (re.test(footprint)) return { g, matched: re.source }
  }
  return { g: DEFAULT_MASS_G, matched: null }
}

function lookupHeight(footprint: string | undefined): { mm: number; matched: string | null } {
  if (!footprint) return { mm: DEFAULT_HEIGHT_MM, matched: null }
  for (const [re, mm] of FOOTPRINT_HEIGHT_MM) {
    if (re.test(footprint)) return { mm, matched: re.source }
  }
  return { mm: DEFAULT_HEIGHT_MM, matched: null }
}

type El = Record<string, any>

function byType(circuitJson: El[], type: string): El[] {
  return circuitJson.filter((e) => e && e.type === type)
}

/** Designator for a pcb_component, resolved through its source_component. */
function refFor(circuitJson: El[], comp: El): string {
  const src = circuitJson.find(
    (e) => e.type === "source_component" && e.source_component_id === comp.source_component_id,
  )
  return src?.name ?? comp.pcb_component_id ?? "?"
}

function footprintFor(circuitJson: El[], comp: El): string | undefined {
  const src = circuitJson.find(
    (e) => e.type === "source_component" && e.source_component_id === comp.source_component_id,
  )
  return src?.footprinter_string ?? src?.footprint ?? src?.ftype
}

/** Which board edge a point is nearest, given the board bounds. */
function nearestEdge(
  x: number,
  y: number,
  minX: number,
  minY: number,
  maxX: number,
  maxY: number,
): Edge {
  const d: Array<[Edge, number]> = [
    ["west", x - minX],
    ["east", maxX - x],
    ["south", y - minY],
    ["north", maxY - y],
  ]
  d.sort((a, b) => a[1] - b[1])
  return d[0][0]
}

export function deriveBoardReport(circuitJson: El[], opts: DeriveOptions = {}): DerivedBoardReport {
  const assumptions: Assumption[] = []

  const board = byType(circuitJson, "pcb_board")[0]
  if (!board) {
    throw new Error(
      "no pcb_board element in Circuit JSON — cannot derive a board_report without an outline",
    )
  }

  const cx = board.center?.x ?? 0
  const cy = board.center?.y ?? 0
  const w = Number(board.width)
  const h = Number(board.height)
  if (!(w > 0 && h > 0)) {
    throw new Error(`pcb_board has a degenerate outline: width=${board.width} height=${board.height}`)
  }

  const minX = cx - w / 2
  const minY = cy - h / 2
  const maxX = cx + w / 2
  const maxY = cy + h / 2

  const thickness = Number(board.thickness) > 0 ? Number(board.thickness) : 1.6
  if (!(Number(board.thickness) > 0)) {
    assumptions.push({
      subject: "board",
      field: "thickness_mm",
      value: 1.6,
      unit: "mm",
      status: "ASSUMED",
      source: "cad/board-report.ts default",
      note: "pcb_board carried no usable thickness",
    })
  }

  // --- mounting holes ---------------------------------------------------
  // A plated hole with no pcb_port_id is mechanical, not electrical. Unplated
  // `pcb_hole` elements are mechanical by definition.
  const mounting_holes: MountingHole[] = []
  for (const ph of byType(circuitJson, "pcb_plated_hole")) {
    if (ph.pcb_port_id) continue
    mounting_holes.push({
      x_mm: Number(ph.x),
      y_mm: Number(ph.y),
      diameter_mm: Number(ph.hole_diameter) || 3.2,
      plated: true,
    })
  }
  for (const hole of byType(circuitJson, "pcb_hole")) {
    mounting_holes.push({
      x_mm: Number(hole.x),
      y_mm: Number(hole.y),
      diameter_mm: Number(hole.hole_diameter ?? hole.diameter) || 3.2,
      plated: false,
    })
  }

  // --- components -------------------------------------------------------
  const component_heightmap: ComponentHeight[] = []
  const connector_edges: ConnectorEdge[] = []
  const componentMasses: Array<{ x: number; y: number; g: number }> = []
  const explicitConnectors = opts.connectorRefs ? new Set(opts.connectorRefs) : null
  const fallbackHeight = opts.defaultHeightMm ?? DEFAULT_HEIGHT_MM

  for (const comp of byType(circuitJson, "pcb_component")) {
    if (comp.do_not_place) continue
    const ref = refFor(circuitJson, comp)
    const footprint = footprintFor(circuitJson, comp)
    const x = Number(comp.center?.x ?? 0)
    const y = Number(comp.center?.y ?? 0)
    const fw = Number(comp.width) || 1
    const fd = Number(comp.height) || 1 // Y extent, despite the name
    const side = comp.layer === "bottom" ? ("bottom" as const) : ("top" as const)

    let heightMm: number
    const override = opts.heightOverrides?.[ref]
    if (override !== undefined) {
      heightMm = override
      assumptions.push({
        subject: ref,
        field: "height_mm",
        value: override,
        unit: "mm",
        status: "CONFIRMED",
        source: "caller-supplied heightOverrides",
        note: footprint ? `footprint ${footprint}` : "",
      })
    } else {
      const { mm, matched } = lookupHeight(footprint)
      heightMm = matched ? mm : fallbackHeight
      assumptions.push({
        subject: ref,
        field: "height_mm",
        value: heightMm,
        unit: "mm",
        status: "ASSUMED",
        source: matched
          ? `cad/board-report.ts footprint table (${matched})`
          : "cad/board-report.ts default",
        note: matched
          ? `typical package height for ${footprint}`
          : `no footprint match${footprint ? ` for ${footprint}` : " and no footprint recorded"}; used fallback`,
      })
    }

    let massG: number
    const massOverride = opts.massOverrides?.[ref]
    if (massOverride !== undefined) {
      massG = massOverride
      assumptions.push({
        subject: ref,
        field: "mass_g",
        value: massOverride,
        unit: "g",
        status: "CONFIRMED",
        source: "caller-supplied massOverrides",
        note: footprint ? `footprint ${footprint}` : "",
      })
    } else {
      const { g, matched } = lookupMass(footprint)
      massG = g
      assumptions.push({
        subject: ref,
        field: "mass_g",
        value: massG,
        unit: "g",
        status: "ASSUMED",
        source: matched
          ? `cad/board-report.ts footprint mass table (${matched})`
          : "cad/board-report.ts default",
        note: matched
          ? `typical populated mass for ${footprint}`
          : `no footprint match${footprint ? ` for ${footprint}` : " and no footprint recorded"}; used fallback`,
      })
    }
    componentMasses.push({ x, y, g: massG })

    component_heightmap.push({
      ref,
      x_mm: x,
      y_mm: y,
      width_mm: fw,
      depth_mm: fd,
      height_mm: heightMm,
      side,
    })

    const looksLikeConnector =
      explicitConnectors?.has(ref) ??
      (/^(J|CN|P)\d/i.test(ref) || /usb|header|pinrow|terminal|jack|conn/i.test(footprint ?? ""))

    if (looksLikeConnector) {
      const edge = nearestEdge(x, y, minX, minY, maxX, maxY)
      connector_edges.push({
        ref,
        edge,
        // Plain board coordinates. These used to be swapped for east/west so that
        // x_mm always held "position along the wall", but the CAD side reads the
        // pair as literal board coordinates (`cad_api.geometry.port_cutouts_for`
        // adds off_x to x_mm and off_y to y_mm), so the swap put every east/west
        // cutout at the connector's x on the y axis. On rover-power that placed
        // J1's opening 2.5mm *below* the cavity floor plane, outside the solid:
        // the boolean subtracted nothing and the connector stayed walled in.
        // check_fit cannot see it — it compares edge, width and height, never
        // position — so the two sides agreed on a cutout that did not exist.
        x_mm: x,
        y_mm: y,
        // The opening's width is measured along the wall, so it is the footprint
        // extent parallel to that wall: X for north/south, Y for east/west.
        width_mm: edge === "north" || edge === "south" ? fw : fd,
        height_mm: heightMm,
        needs_cutout: true,
      })
    }
  }

  // --- thermal ----------------------------------------------------------
  // Circuit JSON carries no power figures. Anything here came from the caller.
  const thermal_hotspots: ThermalHotspot[] = Object.entries(opts.powerByRef ?? {}).map(
    ([ref, power_w]) => {
      const c = component_heightmap.find((e) => e.ref === ref)
      return {
        ref,
        x_mm: c?.x_mm ?? cx,
        y_mm: c?.y_mm ?? cy,
        power_w,
        max_temp_c: null,
      }
    },
  )

  // --- mass -------------------------------------------------------------
  // Substrate first, because it is the honest half: area and thickness come off
  // the routed outline and FR-4's density is a material constant. The board is
  // treated as a full rectangle, which overstates a board with large cutouts —
  // stated here rather than corrected, because Circuit JSON's outline is the
  // bounding rectangle in the first place and a correction would be invented.
  const areaCm2 = ((maxX - minX) * (maxY - minY)) / 100
  const substrate_g = areaCm2 * (thickness / 10) * FR4_DENSITY_G_PER_CM3
  assumptions.push({
    subject: "board",
    field: "substrate_g",
    value: substrate_g,
    unit: "g",
    status: "INFERRED",
    source: "cad/board-report.ts: outline area x thickness x FR-4 density",
    note: `${areaCm2.toFixed(2)}cm^2 x ${thickness}mm x ${FR4_DENSITY_G_PER_CM3} g/cm^3`,
  })

  const components_g = componentMasses.reduce((sum, c) => sum + c.g, 0)
  const total_g = substrate_g + components_g

  // CoM of substrate (at the board centre) and components, mass-weighted. This
  // is the number the robot's mass model reads; getting it wrong moves the whole
  // robot's centre of mass by however far the heaviest connector sits off centre.
  const momentX = substrate_g * cx + componentMasses.reduce((s, c) => s + c.g * c.x, 0)
  const momentY = substrate_g * cy + componentMasses.reduce((s, c) => s + c.g * c.y, 0)
  const mass: BoardMass = {
    total_g,
    substrate_g,
    components_g,
    com_mm: {
      x_mm: total_g > 0 ? momentX / total_g : cx,
      y_mm: total_g > 0 ? momentY / total_g : cy,
    },
  }

  const boardReport: BoardReport = {
    design_id: opts.designId ?? "",
    mass,
    outline_mm: {
      points: [
        { x_mm: minX, y_mm: minY },
        { x_mm: maxX, y_mm: minY },
        { x_mm: maxX, y_mm: maxY },
        { x_mm: minX, y_mm: maxY },
      ],
    },
    thickness_mm: thickness,
    mounting_holes,
    component_heightmap,
    connector_edges,
    keepouts: [],
    thermal_hotspots,
  }

  return { boardReport, assumptions }
}

/** Assumptions worth showing a human before anything gets manufactured. */
export function riskyAssumptions(assumptions: Assumption[]): Assumption[] {
  return assumptions.filter((a) => a.status === "ASSUMED" && a.field === "height_mm")
}

/** One-line-per-assumption summary for run logs. */
export function describeAssumptions(assumptions: Assumption[]): string {
  const risky = riskyAssumptions(assumptions)
  if (risky.length === 0) return "no assumed component heights — every height was supplied"
  const tallest = [...risky].sort((a, b) => b.value - a.value).slice(0, 5)
  return [
    `${risky.length} component height(s) ASSUMED, not measured — the enclosure cavity depends on these:`,
    ...tallest.map((a) => `  ${a.subject.padEnd(8)} ${a.value.toFixed(2)}mm  (${a.source})`),
    risky.length > tallest.length ? `  ... and ${risky.length - tallest.length} more` : "",
  ]
    .filter(Boolean)
    .join("\n")
}
