/**
 * The PCB <-> CAD contract (master-plan.md §6), TypeScript side.
 *
 * This file is the mirror of `cad-generation/api/src/cad_api/contracts.py`.
 * Per §10 it is an H+4 contract file: the two must change in the same commit,
 * and `npm run cad:contract-check` verifies they agree against the live
 * service's OpenAPI schema.
 *
 * Units are **millimetres** on this boundary — the PCB world's native unit.
 * The CAD engine works in SI metres internally and converts at its own edge;
 * no metre ever appears in this file.
 */

import { z } from "zod"

export const Point2 = z.object({ x_mm: z.number(), y_mm: z.number() })

export const BBox2 = z.object({
  min_x_mm: z.number(),
  min_y_mm: z.number(),
  max_x_mm: z.number(),
  max_y_mm: z.number(),
})

export const Outline = z.object({ points: z.array(Point2).min(3) })

export const Edge = z.enum(["north", "south", "east", "west"])
export const Side = z.enum(["top", "bottom"])
export const Severity = z.enum(["blocker", "major", "minor"])

export const MountingHole = z.object({
  x_mm: z.number(),
  y_mm: z.number(),
  diameter_mm: z.number().positive(),
  plated: z.boolean().default(false),
})

export const ComponentHeight = z.object({
  ref: z.string(),
  x_mm: z.number(),
  y_mm: z.number(),
  width_mm: z.number().positive(),
  depth_mm: z.number().positive(),
  /** Z extent above (or below) the board surface. NOT present in Circuit JSON —
   *  see `board-report.ts` for where this number comes from and how confident
   *  we are in it. */
  height_mm: z.number().nonnegative(),
  side: Side.default("top"),
})

export const ConnectorEdge = z.object({
  ref: z.string(),
  edge: Edge,
  x_mm: z.number(),
  y_mm: z.number(),
  width_mm: z.number().positive(),
  height_mm: z.number().positive(),
  needs_cutout: z.boolean().default(true),
})

export const Keepout = z.object({
  name: z.string(),
  x_mm: z.number(),
  y_mm: z.number(),
  width_mm: z.number().positive(),
  depth_mm: z.number().positive(),
  height_mm: z.number().nullable().default(null),
  side: Side.default("top"),
})

export const ThermalHotspot = z.object({
  ref: z.string(),
  x_mm: z.number(),
  y_mm: z.number(),
  power_w: z.number().nonnegative(),
  max_temp_c: z.number().nullable().default(null),
})

/**
 * Mass and centre of mass of the populated board.
 *
 * The robot side (§7.3 of robot-platform-tech-stack.md) takes this as a
 * MEASURED-class fact, replacing the ASSUMED "50 g per board" placeholder its
 * Phase 1 starts with. It belongs here and not there: the BOM and the stackup
 * live on this side, and a robot-side estimate would be a second opinion nobody
 * measured.
 *
 * Substrate and components are reported separately because their provenance
 * differs. The substrate is area x thickness x density, all of which the routed
 * artifact knows. The component figure is only as good as the per-footprint mass
 * table behind it — the same honesty problem, and the same treatment, as the
 * component *height* table this module already carries.
 */
export const BoardMass = z.object({
  total_g: z.number().nonnegative(),
  substrate_g: z.number().nonnegative(),
  components_g: z.number().nonnegative(),
  com_mm: Point2,
})

export const BoardReport = z.object({
  design_id: z.string().default(""),
  outline_mm: Outline,
  thickness_mm: z.number().positive().default(1.6),
  mounting_holes: z.array(MountingHole).default([]),
  component_heightmap: z.array(ComponentHeight).default([]),
  connector_edges: z.array(ConnectorEdge).default([]),
  keepouts: z.array(Keepout).default([]),
  thermal_hotspots: z.array(ThermalHotspot).default([]),
  /** Mass of the populated board. Null when it was not computed — a board of
   *  unknown mass and a massless board are different inputs to the robot's
   *  centre-of-mass calculation and only one of them is possible. */
  mass: BoardMass.nullable().default(null),
})

export const Box3 = z.object({
  length_mm: z.number().positive(),
  width_mm: z.number().positive(),
  height_mm: z.number().positive(),
})

export const Standoff = z.object({
  x_mm: z.number(),
  y_mm: z.number(),
  height_mm: z.number().positive(),
  hole_diameter_mm: z.number().positive(),
  outer_diameter_mm: z.number().positive(),
})

export const PortCutout = z.object({
  ref: z.string(),
  edge: Edge,
  x_mm: z.number(),
  y_mm: z.number(),
  width_mm: z.number().positive(),
  height_mm: z.number().positive(),
})

export const Artifacts = z.object({
  step_path: z.string().default(""),
  glb_path: z.string().default(""),
  stl_path: z.string().default(""),
  content_hash: z.string().default(""),
})

export const EnclosureReport = z.object({
  cavity_mm: Box3,
  standoff_positions: z.array(Standoff).default([]),
  port_cutouts: z.array(PortCutout).default([]),
  wall_thickness_mm: z.number().positive(),
  max_component_height_mm: z.number().nonnegative(),
  /** Where the board's bbox minimum sits in the enclosure's cavity frame.
   *  Without this the two coordinate systems can't be compared. */
  board_origin_mm: Point2.default({ x_mm: 0, y_mm: 0 }),
  outer_mm: Box3.nullable().default(null),
  material: z.string().default("pla"),
  mass_kg: z.number().nullable().default(null),
  artifacts: Artifacts.default({ step_path: "", glb_path: "", stl_path: "", content_hash: "" }),
})

export const Envelope = z.object({
  reason: z.string(),
  max_outline_mm: BBox2,
  max_component_height_mm: z.number().nonnegative(),
  max_bottom_component_height_mm: z.number().nonnegative().default(0),
  mounting_hole_pattern: z.array(MountingHole).default([]),
  keepouts: z.array(Keepout).default([]),
})

export const Violation = z.object({
  code: z.string(),
  severity: Severity,
  detail: z.string(),
  /** measured/limit rather than a bare boolean, so the negotiator can tell a
   *  0.2mm interference from a 40mm one and decide which side moves (§6, §8). */
  measured: z.number(),
  limit: z.number(),
  unit: z.string(),
  ref: z.string().default(""),
})

export const FitResult = z.object({
  ok: z.boolean(),
  violations: z.array(Violation).default([]),
})

export const EnclosureIntent = z.object({
  material: z.string().default("pla"),
  wall_thickness_mm: z.number().positive().default(2.0),
  clearance_mm: z.number().nonnegative().default(1.5),
  lid_thickness_mm: z.number().positive().default(2.0),
  standoff_height_mm: z.number().positive().default(4.0),
  standoff_outer_diameter_mm: z.number().positive().default(6.0),
  headroom_mm: z.number().nonnegative().default(2.0),
  vented: z.boolean().default(false),
  name: z.string().default("enclosure"),
})

export const DesignEnclosureResponse = z.object({
  enclosure_report: EnclosureReport,
  fit: FitResult,
  ir_json: z.record(z.string(), z.any()).nullable().default(null),
  evaluation: z.record(z.string(), z.any()).nullable().default(null),
})

export type Point2 = z.infer<typeof Point2>
export type BBox2 = z.infer<typeof BBox2>
export type Outline = z.infer<typeof Outline>
export type Edge = z.infer<typeof Edge>
export type Side = z.infer<typeof Side>
export type Severity = z.infer<typeof Severity>
export type MountingHole = z.infer<typeof MountingHole>
export type ComponentHeight = z.infer<typeof ComponentHeight>
export type ConnectorEdge = z.infer<typeof ConnectorEdge>
export type Keepout = z.infer<typeof Keepout>
export type ThermalHotspot = z.infer<typeof ThermalHotspot>
export type BoardMass = z.infer<typeof BoardMass>
export type BoardReport = z.infer<typeof BoardReport>
export type Box3 = z.infer<typeof Box3>
export type Standoff = z.infer<typeof Standoff>
export type PortCutout = z.infer<typeof PortCutout>
export type EnclosureReport = z.infer<typeof EnclosureReport>
export type Envelope = z.infer<typeof Envelope>
export type Violation = z.infer<typeof Violation>
export type FitResult = z.infer<typeof FitResult>
export type EnclosureIntent = z.infer<typeof EnclosureIntent>
export type DesignEnclosureResponse = z.infer<typeof DesignEnclosureResponse>
