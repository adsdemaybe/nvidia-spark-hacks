/** The two authoring steps: write the HDL from a spec, and revise it from a work order. */
import { HDL_GUIDE } from "../hdl-guide.ts"
import { askText, extractCode, type ChatLike } from "../model.ts"
import { describeBuild } from "../build.ts"
import { describePhysics, type PhysicsReport } from "../physics/index.ts"
import { describePartsPlan } from "./parts.ts"
import type { PartsPlan, Verdict } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

const SYSTEM = `
You are a hardware engineer who designs printed circuit boards in tscircuit, a
React-flavoured hardware description language.

You are given a design specification and you return one complete .tsx module.
Your output goes straight into a compiler that produces a netlist, a schematic and
an autorouted PCB, all of which are then analysed and reviewed. Design as if the board
is going to be fabricated, because the artifacts from your code are what gets
fabricated.

You are given a parts plan: the topology, the bill of materials, the values and the
arithmetic behind them, already decided. Implement it. Use exactly those parts, those
reference designators, those footprints and those values. If building the board proves
the plan wrong, say so in a comment and deviate deliberately — but do not quietly
substitute a different part, and do not re-derive values it already computed.

What a good answer looks like:
- Every part in the plan, wired to do the job the plan gives it.
- Every pin connected, or deliberately and visibly left open.
- Physical placement chosen on purpose: related parts near each other, decoupling
  caps hard against the pin they serve, connectors at the board edge, nothing
  overlapping, nothing outside the outline.
- A schematic laid out to be read: power at the top, ground at the bottom, signal
  flowing left to right.

Reply with exactly one \`\`\`tsx code block and nothing else.

${HDL_GUIDE}
`.trim()

/** First pass: specification + parts plan -> HDL. */
export async function designFromSpec(
  model: ChatLike,
  spec: string,
  plan?: PartsPlan,
): Promise<string> {
  const raw = await askText(
    model,
    SYSTEM,
    [
      "Build this board.",
      "",
      `<specification>\n${spec}\n</specification>`,
      "",
      plan ? `<parts_plan>\n${describePartsPlan(plan)}\n</parts_plan>` : "",
    ].join("\n"),
  )
  return extractCode(raw)
}

/** Later passes: HDL + analysis + work order -> revised HDL. */
export async function reviseDesign(args: {
  model: ChatLike
  spec: string
  code: string
  build: BuildResult
  physics?: PhysicsReport
  verdict: Verdict
  plan?: PartsPlan
}): Promise<string> {
  const { model, spec, code, build, physics, verdict, plan } = args

  const workOrder = verdict.work_order
    .map(
      (i, n) =>
        `${n + 1}. [${i.severity}/${i.category}] (${i.source}) ${i.description}\n   Fix: ${i.suggested_fix}`,
    )
    .join("\n")

  const raw = await askText(
    model,
    SYSTEM,
    [
      `Revise this design against the work order. Work the items in order, and leave`,
      `everything not named alone — an unrequested change is a regression waiting to`,
      `happen. A fix that introduces a new problem is worse than the issue it closed,`,
      `so check each change against the netlist and the analysis before you make it.`,
      `Where two items interact, satisfy both rather than trading one for the other.`,
      "",
      `<specification>\n${spec}\n</specification>`,
      "",
      plan ? `<parts_plan>\n${describePartsPlan(plan)}\n</parts_plan>\n` : "",
      `<current_hdl>\n${code}\n</current_hdl>`,
      "",
      `<compiler_report>\n${describeBuild(build)}\n</compiler_report>`,
      "",
      physics ? `<analysis>\n${describePhysics(physics)}\n</analysis>\n` : "",
      `<work_order>\n${verdict.summary}\n\n${workOrder}\n</work_order>`,
      "",
      "Return the complete revised module in one ```tsx block.",
    ].join("\n"),
  )
  return extractCode(raw)
}
