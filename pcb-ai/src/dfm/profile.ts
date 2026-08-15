/**
 * Fab profiles — the manufacturing limits a board is checked against (plan §3.6).
 *
 * The plan called for these as hand-written data files. They do not need to be: a real
 * KiCad project already carries exactly this, in `design_settings.rules`, written by
 * whoever set the board up for a real fab house. Reading the profile out of a
 * `.kicad_pro` means the numbers come from a board someone actually ordered rather than
 * from a table someone typed, and it makes "check this design against the same rules as
 * that project" a one-line operation.
 *
 * `flight_controller.kicad_pro` in the repo root is the reference profile: a 4-layer
 * flight controller, which is the closest production analogue to the boards this
 * pipeline designs.
 */
import fs from "node:fs/promises"

export interface FabProfile {
  name: string
  source: string
  /** Millimetres throughout — KiCad's project file is already in mm. */
  min_track_width: number
  min_clearance: number
  min_copper_edge_clearance: number
  min_through_hole_diameter: number
  min_hole_to_hole: number
  min_hole_clearance: number
  min_via_diameter: number
  min_via_annular_width: number
  min_text_height: number
  min_text_thickness: number
  min_silk_clearance: number
  /** Severity by rule id, as KiCad records it. Anything unlisted is an error. */
  severities: Record<string, "error" | "warning" | "ignore">
}

/**
 * Conservative fallback, used when no profile is supplied.
 *
 * These are deliberately *not* the loosest values any fab accepts. A default that
 * passes everything is a default that catches nothing, and the whole point of L8 is to
 * fail before the money is spent.
 */
export const DEFAULT_PROFILE: FabProfile = {
  name: "generic-2layer",
  source: "built-in default",
  min_track_width: 0.15,
  min_clearance: 0.15,
  min_copper_edge_clearance: 0.3,
  min_through_hole_diameter: 0.3,
  min_hole_to_hole: 0.25,
  min_hole_clearance: 0.25,
  min_via_diameter: 0.45,
  min_via_annular_width: 0.1,
  min_text_height: 0.8,
  min_text_thickness: 0.08,
  min_silk_clearance: 0.0,
  severities: {},
}

const num = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback

/** Read a fab profile out of a KiCad `.kicad_pro`. */
export async function loadKicadProProfile(file: string): Promise<FabProfile> {
  const project = JSON.parse(await fs.readFile(file, "utf8"))
  const rules = project?.board?.design_settings?.rules ?? {}
  const severities = project?.board?.design_settings?.rule_severities ?? {}

  // Net-class clearance is the number a router actually honours; the global
  // `min_clearance` is the floor beneath it. Take the stricter of the two so a profile
  // cannot be loosened by leaving one of them unset.
  const classes: any[] = project?.net_settings?.classes ?? []
  const classClearance = classes
    .map((c) => c?.clearance)
    .filter((v): v is number => typeof v === "number" && v > 0)
  const minClearance = Math.max(
    num(rules.min_clearance, DEFAULT_PROFILE.min_clearance),
    ...(classClearance.length ? [Math.min(...classClearance)] : []),
  )

  return {
    name: project?.meta?.filename?.replace(/\.kicad_pro$/, "") ?? file,
    source: file,
    min_track_width: num(rules.min_track_width, DEFAULT_PROFILE.min_track_width),
    min_clearance: minClearance,
    min_copper_edge_clearance: num(
      rules.min_copper_edge_clearance,
      DEFAULT_PROFILE.min_copper_edge_clearance,
    ),
    min_through_hole_diameter: num(
      rules.min_through_hole_diameter,
      DEFAULT_PROFILE.min_through_hole_diameter,
    ),
    min_hole_to_hole: num(rules.min_hole_to_hole, DEFAULT_PROFILE.min_hole_to_hole),
    min_hole_clearance: num(rules.min_hole_clearance, DEFAULT_PROFILE.min_hole_clearance),
    min_via_diameter: num(rules.min_via_diameter, DEFAULT_PROFILE.min_via_diameter),
    min_via_annular_width: num(
      rules.min_via_annular_width,
      DEFAULT_PROFILE.min_via_annular_width,
    ),
    min_text_height: num(rules.min_text_height, DEFAULT_PROFILE.min_text_height),
    min_text_thickness: num(rules.min_text_thickness, DEFAULT_PROFILE.min_text_thickness),
    min_silk_clearance: num(rules.min_silk_clearance, DEFAULT_PROFILE.min_silk_clearance),
    severities,
  }
}

export function describeProfile(p: FabProfile): string {
  return [
    `profile "${p.name}"  (${p.source})`,
    `  track ≥ ${p.min_track_width} mm, clearance ≥ ${p.min_clearance} mm, ` +
      `copper-to-edge ≥ ${p.min_copper_edge_clearance} mm`,
    `  via ⌀ ≥ ${p.min_via_diameter} mm, annular ≥ ${p.min_via_annular_width} mm, ` +
      `drill ≥ ${p.min_through_hole_diameter} mm, hole-to-hole ≥ ${p.min_hole_to_hole} mm`,
    `  silkscreen text ≥ ${p.min_text_height} mm tall, ≥ ${p.min_text_thickness} mm thick`,
  ].join("\n")
}
