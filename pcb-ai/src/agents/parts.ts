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
`.trim()

export function selectParts(args: { model: ChatLike; spec: string }): Promise<PartsPlan> {
  const { model, spec } = args
  return askStructured<PartsPlan>(
    model,
    PartsPlanSchema,
    "parts_plan",
    SYSTEM,
    [
      `<specification>\n${spec}\n</specification>`,
      "Choose the topology and the parts for this board.",
    ].join("\n\n"),
  )
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
  if (plan.risks.length) {
    out.push("", "Risks:")
    for (const r of plan.risks) out.push(`  ${r}`)
  }
  return out.join("\n")
}
