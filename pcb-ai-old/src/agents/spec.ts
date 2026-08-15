/**
 * The specification agent: does this board do what was asked?
 *
 * Kept separate from the layout and physics reviews because a board can be beautifully
 * laid out, thermally comfortable, and the wrong product.
 */
import { askStructured, contentOf, type ChatLike } from "../model.ts"
import { describeBuild } from "../build.ts"
import { ReviewSchema, type Review } from "../schemas.ts"
import type { BuildResult } from "../types.ts"

const SYSTEM = `
You are auditing a board against the specification it was built from. You have the
specification, the HDL, the compiled netlist, and the rendered views.

Go through the specification requirement by requirement and check each one against the
netlist and the drawings. Report:

- Requirements not met: a missing part, a missing function, the wrong connector, the
  wrong count, a constraint on size or layer or placement that was ignored.
- Requirements met only nominally: the part is present but wired so it cannot do the
  job, or a value that does not achieve what the requirement asked for.
- Anything present that the specification did not ask for and that costs area, money
  or reliability.
- Requirements the specification states that the design silently reinterpreted.

Do not review layout quality, thermal behaviour or DRC — other reviewers own those.
Compliance only. Quote the requirement you are checking against in each finding, and
say plainly when the board meets the specification in full.
`.trim()

export async function reviewSpec(args: {
  model: ChatLike
  spec: string
  code: string
  build: BuildResult
}): Promise<Review> {
  const { model, spec, code, build } = args
  const content = await contentOf(
    [
      `<specification>\n${spec}\n</specification>`,
      `<hdl>\n${code}\n</hdl>`,
      `<netlist>\n${describeBuild(build)}\n</netlist>`,
      "The rendered views follow.",
    ],
    build.images,
  )
  return askStructured<Review>(model, ReviewSchema, "review", SYSTEM, content)
}
