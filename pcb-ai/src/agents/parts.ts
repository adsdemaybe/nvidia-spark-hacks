/**
 * The parts agent: decides what the board is made of before anyone draws it.
 *
 * Left to itself, a designer agent picks the topology, the parts, the values and the
 * layout in one pass, and the part choices end up as whatever fell out of writing the
 * netlist. Splitting the decision out forces it to be made on its own terms — with the
 * arithmetic written down and the rejected alternatives named — and gives every later
 * agent a fixed bill of materials to check the board against rather than re-litigating
 * it each iteration.
 */
import { askStructured, type ChatLike } from "../model.ts"
import { FOOTPRINTS } from "../hdl-guide.ts"
import { PartsPlanSchema, type PartsPlan } from "../schemas.ts"
import { PLACEMENT_RULE_GUIDANCE, validateRules } from "../placement/constraints.ts"

const SYSTEM = `
You are the engineer choosing what a board will be built from, before anyone starts
drawing it. You produce the topology, the bill of materials and the values. Someone
else turns that into a schematic and a layout, and they will follow your plan exactly,
so anything you leave vague gets decided badly downstream.

Work in this order:

1. **Topology.** Pick the circuit approach and name it. Say why it beats the
   alternatives for this specific requirement — cost, part count, precision, supply
   range, availability — and list what you rejected and why. A 555, a microcontroller
   and a discrete multivibrator are all valid ways to blink an LED and they are not
   equally right for a given brief.

2. **Rails.** Every supply the board needs, its voltage, and what it feeds.

3. **Parts.** Every component, with a reference designator, a value, and a footprint.
   Include the passives real hardware needs and that specifications never mention:
   decoupling for every IC, current limiting for every LED, pull-ups on every reset
   and enable that needs one, bulk capacitance behind every regulator.

4. **The arithmetic.** Show the calculation behind every value that came from one.
   Timing equations with the numbers substituted, current-limit sizing from the actual
   forward voltage and supply, divider ratios. A value with no working behind it is a
   guess, and the reviewers downstream will treat it as one.

5. **Layout constraints and risks.** What must sit at a board edge, what must be
   adjacent to what, how big the board should be. Then what could go wrong with these
   choices: tolerance stack-up, a part that is hard to source, a package that is hard
   to hand-solder, an assumption about the supply that might not hold.

Rules:
- Choose real, ordinary parts. Prefer the jellybean that has been in production for
  twenty years over the clever one, unless the specification demands otherwise.
- Every footprint must be one the downstream toolchain knows. Valid strings:
  ${FOOTPRINTS}
- Pick packages that match the job. Hand-assembled prototypes want 0603 and SOIC;
  0201 and QFN are for machine assembly, so only choose them if the board is dense
  enough to need them.
- Give reference designators by convention: R for resistors, C for capacitors,
  L inductors, D diodes and LEDs, Q transistors, U ICs, Y crystals, J connectors,
  SW switches, TP test points.
- Do not design the layout. Placement is someone else's job; you set the constraints
  it has to satisfy.

${PLACEMENT_RULE_GUIDANCE}
`.trim()

export async function selectParts(args: {
  model: ChatLike
  spec: string
}): Promise<{ plan: PartsPlan; ruleProblems: string[] }> {
  const { model, spec } = args
  const plan = await askStructured<PartsPlan>(
    model,
    PartsPlanSchema,
    "parts_plan",
    SYSTEM,
    [
      `<specification>\n${spec}\n</specification>`,
      "Choose the topology and the parts for this board.",
    ].join("\n\n"),
  )

  // Structural check on the rules the moment they arrive. Zod guarantees the shape of
  // each field; it cannot tell that `opposite_edges` was given three components, or
  // that `why` says "board edge". Catching it here costs milliseconds — catching it at
  // L3' costs a compile, a route and a solve first.
  const ruleProblems = [...validateRules(plan.placement_rules ?? []), ...validateRefs(plan)]
  return { plan, ruleProblems }
}

/**
 * Every `ref` names exactly one physical part.
 *
 * A multi-channel board makes a model want to write `J2-J6` or `Q1..Q5` in one row, and
 * `ref: z.string()` accepts it happily. Nothing downstream does: a placement rule about
 * "J3" matches nothing, the netlist has one connector where the board has five, and the
 * BOM undercounts. The first board this was tried on -- a five-finger servo driver --
 * collapsed fifteen parts into three rows exactly this way.
 *
 * A hand is the shape that induces it: five identical channels is the normal case, not
 * the exotic one. So the check is not "reject a weird string", it is "say what to write
 * instead", because the model's instinct is reasonable and only the notation is wrong.
 */
export function validateRefs(plan: PartsPlan): string[] {
  const problems: string[] = []
  const seen = new Map<string, number>()

  for (const part of plan.parts ?? []) {
    const ref = (part.ref ?? "").trim()
    if (!ref) {
      problems.push(`a part has no ref (${part.role ?? part.value ?? "unnamed"})`)
      continue
    }
    // en-dash, em-dash, hyphen, "to", ".." and "," all show up in practice.
    const range = ref.match(/^([A-Za-z]+)\s*([0-9]+)\s*(?:[-\u2010-\u2015]|\.\.|,|\bto\b)\s*(?:[A-Za-z]+)?\s*([0-9]+)$/)
    if (range) {
      const [, prefix, from, to] = range
      const a = Number(from)
      const b = Number(to)
      const expanded = a <= b && b - a < 64
        ? Array.from({ length: b - a + 1 }, (_, i) => `${prefix}${a + i}`).join(", ")
        : `${prefix}${a}, ${prefix}${a + 1}, ...`
      problems.push(
        `part ref "${ref}" is a range, not a reference designator. One row per physical ` +
          `part: write ${expanded} as separate entries, each with its own footprint and ` +
          `placement. A rule naming "${prefix}${a + 1}" matches nothing while this row exists.`,
      )
      continue
    }
    if (!/^[A-Za-z]{1,3}[0-9]+$/.test(ref)) {
      problems.push(
        `part ref "${ref}" is not a reference designator. Expected letters then digits, ` +
          `e.g. R1, C12, U3, J5.`,
      )
      continue
    }
    seen.set(ref, (seen.get(ref) ?? 0) + 1)
  }

  for (const [ref, n] of seen) {
    if (n > 1) problems.push(`ref "${ref}" is used by ${n} parts; each must be unique.`)
  }
  return problems
}

/** Render the plan for the prompts that consume it. */
export function describePartsPlan(plan: PartsPlan): string {
  const out: string[] = [
    `Topology: ${plan.topology}`,
    `  ${plan.rationale}`,
  ]
  if (plan.alternatives_rejected.length) {
    out.push("  Rejected: " + plan.alternatives_rejected.join("; "))
  }

  out.push("", "Rails:")
  for (const r of plan.rails) out.push(`  ${r.name}  ${r.voltage_v}V — ${r.role}`)

  out.push("", `Parts (${plan.parts.length}):`)
  for (const p of plan.parts) {
    out.push(
      `  ${p.ref}  ${p.kind}  ${p.value}  [${p.footprint}]`,
      `      role: ${p.role}`,
      `      specs: ${p.key_specs}`,
      `      why: ${p.rationale}`,
    )
  }

  if (plan.key_calculations.length) {
    out.push("", "Calculations:")
    for (const c of plan.key_calculations) out.push(`  ${c}`)
  }
  if (plan.layout_constraints.length) {
    out.push("", "Layout constraints:")
    for (const c of plan.layout_constraints) out.push(`  ${c}`)
  }
  if (plan.placement_rules?.length) {
    out.push("", "Placement rules (these are checked against the routed board):")
    for (const r of plan.placement_rules) {
      const detail = [
        r.edge ? `edge=${r.edge}` : "",
        r.layer ? `layer=${r.layer}` : "",
        r.axis ? `axis=${r.axis}` : "",
        r.max_mm != null ? `max=${r.max_mm}mm` : "",
      ]
        .filter(Boolean)
        .join(" ")
      out.push(`  ${r.kind}(${r.refs.join(", ")})${detail ? ` ${detail}` : ""} — ${r.why}`)
    }
  }
  if (plan.risks.length) {
    out.push("", "Risks:")
    for (const r of plan.risks) out.push(`  ${r}`)
  }
  return out.join("\n")
}
