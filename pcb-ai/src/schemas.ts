/**
 * Zod schemas for every structured model call.
 *
 * Zod rather than raw JSON Schema because `withStructuredOutput` is the one interface
 * every LangChain provider implements, and it takes a Zod schema — the provider then
 * turns it into whatever it needs (a tool definition, a response format, a grammar).
 */
import { z } from "zod"
import { PlacementRuleSchema } from "./placement/constraints.ts"

export const SEVERITIES = ["blocker", "major", "minor"] as const

export const CATEGORIES = [
  "connectivity",
  "placement",
  "routing",
  "footprint",
  "electrical",
  "thermal",
  "power-integrity",
  "manufacturability",
  "spec-compliance",
  "silkscreen",
] as const

export const FindingSchema = z.object({
  severity: z
    .enum(SEVERITIES)
    .describe(
      "blocker: the board will not work or cannot be built. major: materially wrong but functional. minor: cosmetic.",
    ),
  category: z.enum(CATEGORIES),
  description: z
    .string()
    .describe("What is wrong, naming the specific components, nets or numbers involved."),
  suggested_fix: z.string().describe("The concrete change to make in the HDL."),
})

export const ReviewSchema = z.object({
  summary: z.string().describe("Two or three sentences on what you found, in your area only."),
  findings: z.array(FindingSchema),
})

export const WorkOrderItemSchema = FindingSchema.extend({
  source: z
    .string()
    .describe(
      "Which reviewer or checker this came from: physics, layout, spec, rules, or several joined by +.",
    ),
})

export const VerdictSchema = z.object({
  pass: z.boolean().describe("True only when no blocker or major items remain."),
  summary: z
    .string()
    .describe("Three or four sentences: the state of the board and what happens next."),
  work_order: z.array(WorkOrderItemSchema),
})

export const OperatingPointSchema = z.object({
  ambient_c: z.number().describe("Ambient temperature in Celsius, typically 25."),
  rails: z.array(
    z.object({
      net: z.string().describe("Net name exactly as it appears in the netlist."),
      voltage_v: z.number(),
      source_pin: z
        .string()
        .describe("Pin that feeds the rail, in Component.pin form, e.g. J1.pin1."),
      max_drop_mv: z
        .number()
        .describe("Largest acceptable IR drop on this rail, in millivolts."),
    }),
  ),
  loads: z.array(
    z.object({
      pin: z.string().describe("Current-drawing pin, e.g. U1.VCC."),
      net: z.string(),
      current_a: z.number().describe("Steady-state current in amperes."),
    }),
  ),
  dissipation: z.array(
    z.object({
      component: z.string(),
      power_w: z.number().describe("Steady-state dissipation in watts."),
      max_temp_c: z
        .number()
        .describe("Maximum operating temperature from the datasheet, in Celsius."),
    }),
  ),
  assumptions: z
    .array(z.string())
    .describe(
      "Every assumption behind the numbers: supply voltage, duty cycle, LED forward voltage, quiescent currents, worst case vs typical.",
    ),
})

export const PartSchema = z.object({
  ref: z.string().describe("Reference designator, e.g. U1, R3, C2, D1, J1."),
  kind: z
    .string()
    .describe(
      "tscircuit element to use: resistor, capacitor, inductor, diode, led, chip, crystal, pushbutton, pinheader, connector, transistor, mosfet, fuse, testpoint.",
    ),
  value: z
    .string()
    .describe(
      "Value with units, or the part number for an IC. E.g. 10k, 100nF, NE555, 16MHz.",
    ),
  footprint: z
    .string()
    .describe("tscircuit footprint string, e.g. 0402, soic8, sod123, pinrow2."),
  role: z.string().describe("What this part does in the circuit, one line."),
  key_specs: z
    .string()
    .describe(
      "The ratings that make this part the right choice: voltage rating, tolerance, forward voltage, power rating, package size.",
    ),
  rationale: z
    .string()
    .describe("Why this part and this package rather than the obvious alternative."),
})

export const PartsPlanSchema = z.object({
  topology: z
    .string()
    .describe("The circuit approach chosen, named, e.g. '555 astable multivibrator'."),
  rationale: z
    .string()
    .describe("Why this topology beats the alternatives for this specification."),
  alternatives_rejected: z
    .array(z.string())
    .describe("Approaches considered and the reason each was not chosen."),
  rails: z.array(
    z.object({
      name: z.string().describe("Net name, e.g. VCC, GND, V3V3."),
      voltage_v: z.number(),
      role: z.string(),
    }),
  ),
  parts: z.array(PartSchema),
  key_calculations: z
    .array(z.string())
    .describe(
      "The arithmetic behind the values: timing equations, current-limit sizing, divider ratios. Show the numbers.",
    ),
  layout_constraints: z
    .array(z.string())
    .describe(
      "Physical requirements the layout must satisfy: board size, what sits at an edge, what must be adjacent to what.",
    ),
  // The prose above is for the designer to read. This is what actually gates — see
  // src/placement/constraints.ts for why both exist.
  placement_rules: z
    .array(PlacementRuleSchema)
    .describe(
      "The same physical requirements, expressed so a tool can check them after routing. " +
        "Every constraint the specification states about where a part sits should appear here.",
    ),
  risks: z
    .array(z.string())
    .describe("What could go wrong with this choice of parts, and what to watch for."),
})

export type Part = z.infer<typeof PartSchema>
export type PartsPlan = z.infer<typeof PartsPlanSchema>
export type Review = z.infer<typeof ReviewSchema>
export type Verdict = z.infer<typeof VerdictSchema>
export type OperatingPoint = z.infer<typeof OperatingPointSchema>
