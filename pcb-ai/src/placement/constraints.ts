/**
 * Placement rules — the part of the layout that is a requirement, not a preference.
 *
 * The parts agent has always emitted `layout_constraints`, and they are good:
 *
 *     "J1 (power header) at one short edge (e.g., bottom edge)."
 *     "J2 (signal header) at the opposite short edge (e.g., top edge)."
 *     "All components on the top layer only."
 *
 * They were also **prose**, and the only code that touched them printed them into the
 * designer's prompt. Nothing measured the result. So a board could come back with both
 * connectors on the same edge, or on the wrong side entirely, and every gate would pass
 * it — the netlist is identical, the routing is clean, the physics is fine, and the fab
 * can build it. The board is simply not the board that was asked for.
 *
 * That is an agent asserting a physical fact with nothing to check it, which is the one
 * thing this pipeline is built to prevent. So placement requirements become a **closed
 * grammar the agent composes and the harness checks** — the same shape as the L7 claims
 * and, deliberately, the same shape the master plan uses for F3's success predicates:
 * the agent chooses which rules apply and to what, and invents no new kinds.
 *
 * `layout_constraints` stays as prose for the designer to read. `placement_rules` is
 * what gates.
 */
import { z } from "zod"

export const EDGES = ["left", "right", "top", "bottom"] as const
export type Edge = (typeof EDGES)[number]

export const PlacementRuleSchema = z.object({
  kind: z
    .enum(["at_edge", "opposite_edges", "same_edge", "on_layer", "adjacent", "in_row"])
    .describe(
      "at_edge: a part must sit against a board edge. " +
        "opposite_edges: two parts must be on opposing edges (left/right or top/bottom). " +
        "same_edge: parts must share one edge. " +
        "on_layer: a part — or every part, with refs ['*'] — must be on a given layer. " +
        "adjacent: two parts must be within a distance of each other. " +
        "in_row: parts must line up along an axis.",
    ),
  refs: z
    .array(z.string())
    .describe(
      "Reference designators the rule applies to, e.g. ['J1'] or ['J1','J2']. " +
        "Use ['*'] with on_layer to mean every part.",
    ),
  // Nullable-and-required, never optional.
  //
  // Every other schema in this pipeline has all-required fields, and that is not a
  // style preference: the OpenAI structured-output API that vLLM implements rejects
  // `.optional()` outright ("uses .optional() without .nullable()"), so an optional
  // field fails at schema-construction time against a real server while passing
  // silently against the offline stub. A field that does not apply to a given rule is
  // emitted as null.
  edge: z
    .enum(["left", "right", "top", "bottom", "any"])
    .nullable()
    .describe(
      "For at_edge/same_edge: which edge, or 'any' when only proximity matters. " +
        "null for every other rule kind.",
    ),
  layer: z
    .enum(["top", "bottom"])
    .nullable()
    .describe("For on_layer. null otherwise."),
  max_mm: z
    .number()
    .nullable()
    .describe(
      "For at_edge/same_edge: how close counts as 'at' the edge (default 3). " +
        "For adjacent: the maximum centre-to-centre distance. " +
        "For in_row: the allowed spread across the axis (default 1). null to take the default.",
    ),
  axis: z
    .enum(["x", "y"])
    .nullable()
    .describe("For in_row: the axis the parts line up along. null otherwise."),
  why: z.string().describe("What breaks if this is not true. One sentence."),
})

export type PlacementRule = z.infer<typeof PlacementRuleSchema>

export const PLACEMENT_RULE_GUIDANCE = `
Placement rules are machine-checked after the board is routed, so write only what is
genuinely required and write it precisely. A rule you cannot defend is a rule that will
block a good board.

Emit a rule for every physical requirement the specification states, in particular:
- a connector the spec puts at an edge -> at_edge
- two connectors the spec puts on opposite edges -> opposite_edges
- "all parts on the top layer" -> on_layer with refs ["*"] and layer "top"
- a decoupling capacitor that must sit against the pin it serves -> adjacent
- parts the spec wants lined up (indicator LEDs in a row) -> in_row

Name real reference designators from the bill of materials. Do not invent rules the
specification does not ask for: every rule is a gate, and an over-specified board fails
for reasons nobody asked about.
`.trim()
