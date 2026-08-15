/** The two authoring steps: write the HDL from a spec, and revise it from a work order. */
import { HDL_GUIDE } from "../hdl-guide.ts"
import { askText, extractCode, type ChatLike } from "../model.ts"
import {
  applyEditBlocks,
  looksTruncated,
  parseEditBlocks,
  EDIT_BLOCK_INSTRUCTIONS,
} from "../hdl-patch.ts"
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

/**
 * The revise-time system prompt.
 *
 * Deliberately not `SYSTEM`: that one carries the full HDL guide (~2.2k tokens), which
 * earns its place when writing a board from nothing and does not when the model already
 * has a working module of the same dialect in front of it. Combined with edit blocks,
 * dropping it takes the revise turn from ~14.3k tokens of prompt-plus-output down to
 * roughly 10k — the difference between a reasoning model having room to think and
 * having its answer truncated mid-file.
 */
const REVISE_SYSTEM = `
You are a hardware engineer revising a tscircuit board against a work order.

The current module is given to you in full. It compiles, it routes, and it has been
measured — the reports you are shown are facts from tools, not opinions. Change only
what the work order names. An unrequested change is a regression waiting to happen, and
a fix that introduces a new problem is worse than the issue it closed. Where two items
interact, satisfy both rather than trading one for the other.

${EDIT_BLOCK_INSTRUCTIONS}
`.trim()

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

  // Only the top items go to the designer; the rest carry to the next iteration.
  //
  // Two reasons, and the second is the one that showed up in practice. The obvious one
  // is budget: an eleven-item work order is ~4k characters of prompt and, far more
  // expensively, eleven things for a reasoning model to hold at once — which is how a
  // revision ends up truncated mid-edit. The better one is that the loop's own
  // instructions say a fix that introduces a new problem is worse than the issue it
  // closed. Fewer, more careful changes per pass is what that advice looks like when it
  // is enforced rather than merely requested. The work order is already severity-sorted,
  // so this takes the blockers first.
  const MAX_ITEMS_PER_PASS = 4
  const selected = verdict.work_order.slice(0, MAX_ITEMS_PER_PASS)
  const deferred = verdict.work_order.length - selected.length

  const workOrder =
    selected
      .map(
        (i, n) =>
          `${n + 1}. [${i.severity}/${i.category}] (${i.source}) ${i.description}\n   Fix: ${i.suggested_fix}`,
      )
      .join("\n") +
    (deferred > 0
      ? `\n\n(${deferred} lower-severity item(s) held back for the next pass — do not act on them now.)`
      : "")

  const raw = await askText(
    model,
    REVISE_SYSTEM,
    [
      `Revise this design against the work order, working the items in order.`,
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
      "Return only edit blocks.",
    ].join("\n"),
  )

  const edits = parseEditBlocks(raw)
  if (edits.length) {
    const { code: revised, notes } = applyEditBlocks(code, edits)
    for (const note of notes) console.log(`            ${note}`)
    console.log(`            applied ${edits.length} edit block(s)`)
    return revised
  }

  if (looksTruncated(raw)) {
    throw new Error(
      "Revision was cut off mid-edit-block: an edit was opened and never closed. " +
        "This is an output-budget failure, not a design error — raise the server's " +
        "--max-model-len.",
    )
  }

  // A model that ignores the format and returns a whole module is still trying to be
  // useful; take it rather than failing the iteration. extractCode does its own
  // truncation checks.
  return extractCode(raw)
}
