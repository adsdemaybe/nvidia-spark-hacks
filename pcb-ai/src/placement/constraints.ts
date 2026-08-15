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

/**
 * The four edges of the board OUTLINE, named by compass point.
 *
 * This started as left/right/top/bottom and caused a real misreading: "top" and
 * "bottom" mean two unrelated things in PCB work — an edge of the rectangle in plane,
 * and the copper face a part is soldered to. A report saying "J5 bottom edge" was read
 * as "J5 is on the bottom of the board", which is a different axis entirely. Prose
 * disambiguation helps and is still there; sharing the words at all was the mistake.
 *
 * `src/cad/contracts.ts` had already got this right — `Edge = north|south|east|west`
 * beside `Side = top|bottom` — so this adopts that convention rather than inventing a
 * third. Two modules in one directory disagreeing about vocabulary is worse than either
 * choice.
 *
 *   north = y-max    south = y-min    east = x-max    west = x-min
 *
 * The copper side stays `layer: top | bottom`, and now cannot be confused with an edge.
 */
export const EDGES = ["north", "south", "east", "west"] as const
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
    .enum(["north", "south", "east", "west", "any"])
    .nullable()
    .describe(
      "For at_edge/same_edge: which edge of the board outline, by compass point — " +
        "north = y-max, south = y-min, east = x-max, west = x-min — or 'any' when only " +
        "proximity matters. Compass points deliberately avoid top/bottom, which name " +
        "the copper side (`layer`), not an edge. null for every other rule kind.",
    ),
  layer: z
    .enum(["top", "bottom"])
    .nullable()
    .describe(
      "For on_layer: which copper SIDE the part is soldered to. Only emit this rule " +
        "when the specification actually asks for single-sided assembly — bottom-side " +
        "connectors are a normal design choice, not a defect. null otherwise.",
    ),
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

/**
 * Structural validation, run the moment the parts plan arrives.
 *
 * The grammar constrains syntax, not sense. A weak model emits well-formed rules that
 * are nonsense — `opposite_edges` over three parts, an `at_edge` with no edge, a `why`
 * that says "board edge". None of that is caught by Zod, and waiting for L3' to catch
 * it means five stages of compiling, routing and solving before anyone learns the plan
 * was malformed. Principle 3: fail at the earliest, cheapest stage.
 *
 * This checks *shape*, which is all a tool can check here. Whether the rule is the
 * right rule for the specification is the spec reviewer's job.
 */
export function validateRules(rules: PlacementRule[]): string[] {
  const problems: string[] = []

  rules.forEach((rule, i) => {
    const at = `placement rule ${i + 1} (${rule.kind})`

    if (!rule.refs?.length) {
      problems.push(`${at}: names no components`)
      return
    }
    if (rule.refs.includes("*") && rule.kind !== "on_layer") {
      problems.push(`${at}: the "*" wildcard is only meaningful for on_layer`)
    }

    const exactlyTwo = ["opposite_edges", "adjacent"]
    if (exactlyTwo.includes(rule.kind) && rule.refs.length !== 2) {
      problems.push(
        `${at}: needs exactly two components, got ${rule.refs.length} ` +
          `(${rule.refs.join(", ")}). Split it into one rule per pair.`,
      )
    }
    if (rule.kind === "in_row" && rule.refs.length < 2) {
      problems.push(`${at}: needs at least two components to form a row`)
    }
    if (rule.kind === "at_edge" && !rule.edge) {
      problems.push(`${at}: no edge given — use "any" if only proximity matters`)
    }
    if (rule.kind === "on_layer" && !rule.layer) {
      problems.push(`${at}: no layer given`)
    }
    if (rule.kind === "in_row" && !rule.axis) {
      problems.push(`${at}: no axis given — which direction do these line up along?`)
    }
    if (rule.kind === "adjacent" && (rule.max_mm == null || rule.max_mm <= 0)) {
      problems.push(`${at}: needs a positive max_mm — "near" is not a measurement`)
    }
    if ((rule.why ?? "").trim().length < 12) {
      problems.push(
        `${at}: "why" is ${JSON.stringify(rule.why ?? "")} — state what breaks if this ` +
          `is not true, so a reviewer can judge whether the rule is worth failing a board for`,
      )
    }
  })

  return problems
}
