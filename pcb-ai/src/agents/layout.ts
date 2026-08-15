/**
 * The layout agent: looks at the board the way a reviewer at a whiteboard would.
 *
 * Everything it catches is visual — the netlist and the solvers already have the
 * facts, so this agent is pointed only at what a picture shows and a table does not.
 */
import { askStructured, contentOf, supportsVision, type ChatLike } from "../model.ts"
import { describeBuild } from "../build.ts"
import { describeLayout } from "../layout-digest.ts"
import { ReviewSchema, type Review } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

const SYSTEM = `
You are reviewing the physical layout and schematic of a board before fabrication.
You are shown the rendered schematic, the rendered PCB and an assembly view, plus the
netlist.

Connectivity, thermal and DRC are checked elsewhere and are not your job. Yours is
what the drawings show and a table does not:

- Placement that makes no sense: related parts scattered, a connector buried in the
  middle of the board, parts crowded into one corner with the rest empty, a part
  rotated so its silkscreen is unreadable.
- Routing that took an absurd path: a trace looping across the board to reach a pad
  2mm away, needless layer changes, copper squeezed between pads where there was room
  to go around, traces running under a chip body when they did not need to.
- Assembly problems: not enough room for a soldering iron or a pick-and-place nozzle,
  a tall part shadowing a shorter one, a test point that cannot be probed.
- A schematic that cannot be read: crossing wires, no power-at-top ground-at-bottom
  convention, no left-to-right signal flow, components with no visible values.
- Missing, overlapping or off-board silkscreen reference designators.

Judge what you can see. If a concern depends on a number rather than the drawing,
leave it to the analyses that own it. An empty findings list is the right answer for a
well-laid-out board.
`.trim()

export async function reviewLayout(args: {
  model: ChatLike
  spec: string
  build: BuildResult
}): Promise<Review> {
  const { model, spec, build } = args
  const content = await contentOf(
    [
      `<specification>\n${spec}\n</specification>`,
      `<netlist>\n${describeBuild(build)}\n</netlist>`,
      "The rendered views follow.",
    ],
    build.images,
    { vision: supportsVision(model), digest: describeLayout(build) },
  )
  return askStructured<Review>(model, ReviewSchema, "review", SYSTEM, content)
}
