/**
 * The chief engineer: merges the reviews into one decision and one work order.
 *
 * Three reviewers looking at the same board raise overlapping findings, contradict
 * each other, and disagree on severity. Handing that straight to the designer produces
 * churn — a fix for one finding undoing another. This agent resolves it into an
 * ordered list of changes, and is the only step allowed to say the board is done.
 */
import { askStructured, type ChatLike } from "../model.ts"
import { describeBuild } from "../build.ts"
import { describePhysics, physicsBlockers, type PhysicsReport } from "../physics/index.ts"
import { describeSpice, type SpiceReport } from "../spice/index.ts"
import { describeDfm, dfmBlockers, type DfmReport } from "../dfm/index.ts"
import { VerdictSchema, type Review, type Verdict } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

const SYSTEM = `
You are the engineer signing off a board. Independent reviewers have looked at the
specification, the layout and the physics, and a rule checker has produced findings
that are facts rather than opinions. You decide what happens next.

Produce one ordered work order:

- Merge duplicates. Three reviewers describing the same decoupling problem is one item.
- Resolve contradictions. If one reviewer wants a part moved for thermal reasons and
  another wants it moved somewhere else for routing, decide which wins and say so in
  the fix.
- Drop findings the evidence does not support. A reviewer speculating about a problem
  the netlist or the solver output contradicts is noise, and shipping it to the
  designer wastes an iteration.
- Order by what actually blocks the board, not by who raised it.
- Make each fix concrete enough to apply without re-deriving it: name the component,
  the net, the value, the position.
- Never ask for a change that undoes an earlier fix. Where two items interact, say so
  in the fix text.

Set pass=true only when nothing blocking or major remains. A rule-checker error or an
exceeded rating is always blocking and can never be waived — those are measurements,
not opinions. Minor items alone do not block a board; list them and pass.
`.trim()

export async function decide(args: {
  model: ChatLike
  spec: string
  build: BuildResult
  physics?: PhysicsReport
  spice?: SpiceReport
  dfm?: DfmReport
  reviews: Record<string, Review>
}): Promise<Verdict> {
  const { model, spec, build, physics, spice, dfm, reviews } = args

  const reviewText = Object.entries(reviews)
    .map(([name, r]) => {
      const items = r.findings
        .map(
          (f, i) =>
            `  ${i + 1}. [${f.severity}/${f.category}] ${f.description}\n     Fix: ${f.suggested_fix}`,
        )
        .join("\n")
      return `<review source="${name}">\n${r.summary}\n${items || "  (no findings)"}\n</review>`
    })
    .join("\n\n")

  // L6 and L7 blockers are the same kind of thing — a deterministic tool measured a
  // failure — so they enter the chief's evidence and its override through one list.
  const blockers = [
    ...(physics ? physicsBlockers(physics) : []),
    ...(spice?.hardFailures ?? []),
    ...(dfm ? dfmBlockers(dfm) : []),
  ]

  const verdict = await askStructured<Verdict>(
    model,
    VerdictSchema,
    "verdict",
    SYSTEM,
    [
      `<specification>\n${spec}\n</specification>`,
      `<netlist>\n${describeBuild(build)}\n</netlist>`,
      physics ? `<physics>\n${describePhysics(physics)}\n</physics>` : "",
      spice?.available ? `<circuit_simulation>\n${describeSpice(spice)}\n</circuit_simulation>` : "",
      dfm ? `<manufacturability>\n${describeDfm(dfm)}\n</manufacturability>` : "",
      blockers.length
        ? `<hard_failures source="rule checker and solvers, not opinions">\n${blockers
            .map((b) => `  - ${b}`)
            .join("\n")}\n</hard_failures>`
        : "<hard_failures>none</hard_failures>",
      reviewText,
      "Produce the verdict and the work order.",
    ]
      .filter(Boolean)
      .join("\n\n"),
  )

  // A measured failure is not a matter of opinion: if the checkers found one, the
  // board does not pass regardless of what the verdict says.
  if (blockers.length && verdict.pass) {
    verdict.pass = false
    verdict.summary = `${verdict.summary} (Overridden: ${blockers.length} hard failure(s) from the rule checker or solvers remain.)`
  }
  return verdict
}
