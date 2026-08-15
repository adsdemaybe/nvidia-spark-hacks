#!/usr/bin/env tsx
/**
 * Size a board to what is actually on it.
 *
 *   npx tsx tools/autosize.ts runs/<dir>/iter-0/circuit.json            # report
 *   npx tsx tools/autosize.ts runs/<dir>/iter-0/circuit.json --apply examples/board.tsx
 *
 * Boards written by hand acquire slack: an outline is chosen before the parts are
 * placed, the placement lands smaller than expected, and nothing complains because a
 * board that is too big still routes, still passes DRC, and still fabricates. It just
 * costs more and takes more room on the robot — `rover-indicator` was using 47% of its
 * own area.
 *
 * The same measurement catches the opposite mistake, which is not cosmetic at all:
 * parts that hang *off* the edge. Those were sitting 0.04-0.58 mm outside on three of
 * these boards, small enough that the compiler's own placement check let them through.
 *
 * The suggested outline is the parts' extent plus a margin, and the margin is taken from
 * the fab profile rather than invented — copper-to-edge clearance is a manufacturing
 * limit, so the smallest honest board is exactly that far outside the outermost copper.
 *
 * **The limitation worth knowing before trusting the number.** This measures the copper
 * of the route that *already exists*, and shrinking the outline changes the route. The
 * power board measured "48 x 29 is enough" and then failed via-to-pad clearance twice at
 * that size, because the router had less room to work in and put vias where pads were.
 * So the suggestion is a floor to aim at, not a size to adopt unverified: apply it,
 * recompile, and let the gates decide. On these three boards the parts set the width and
 * the *routing* set the height.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { parseArgs } from "node:util"
import { extractPlacement } from "../src/placement/check.ts"
import { loadKicadProProfile, DEFAULT_PROFILE } from "../src/dfm/profile.ts"

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    apply: { type: "string" },
    "fab-profile": { type: "string" },
    /** Extra room beyond the fab minimum, for mounting hardware and handling. */
    margin: { type: "string", default: "1.5" },
    round: { type: "string", default: "1" },
  },
})

const circuitPath = positionals[0]
if (!circuitPath) {
  console.error("usage: autosize.ts <circuit.json> [--apply <board.tsx>] [--margin mm]")
  process.exit(1)
}

const profile = values["fab-profile"]
  ? await loadKicadProProfile(values["fab-profile"])
  : DEFAULT_PROFILE

const circuitJson = JSON.parse(await fs.readFile(circuitPath, "utf8"))
const board = circuitJson.find((e: any) => e?.type === "pcb_board")
if (!board) {
  console.error("no pcb_board in this circuit — nothing to size")
  process.exit(1)
}

const parts = extractPlacement(circuitJson)
if (!parts.length) {
  console.error("no placed components — nothing to size against")
  process.exit(1)
}

// Copper extends past the courtyards: pads, traces and vias all have to fit inside the
// outline too, and on a small board the routing is often what sets the size.
const copper: Array<{ x: number; y: number; r: number }> = []
for (const e of circuitJson as any[]) {
  if (e?.type === "pcb_smtpad") {
    copper.push({ x: e.x, y: e.y, r: Math.max(e.width ?? 0, e.height ?? 0, e.radius ?? 0) / 2 })
  } else if (e?.type === "pcb_plated_hole") {
    copper.push({ x: e.x, y: e.y, r: (e.outer_diameter ?? e.hole_diameter ?? 0) / 2 })
  } else if (e?.type === "pcb_via") {
    copper.push({ x: e.x, y: e.y, r: (e.outer_diameter ?? 0) / 2 })
  } else if (e?.type === "pcb_trace") {
    for (const p of e.route ?? []) {
      if (typeof p.x === "number") copper.push({ x: p.x, y: p.y, r: (p.width ?? 0.15) / 2 })
    }
  }
}

const lo = { x: Infinity, y: Infinity }
const hi = { x: -Infinity, y: -Infinity }
const grow = (x: number, y: number, rx: number, ry: number) => {
  lo.x = Math.min(lo.x, x - rx)
  lo.y = Math.min(lo.y, y - ry)
  hi.x = Math.max(hi.x, x + rx)
  hi.y = Math.max(hi.y, y + ry)
}
for (const p of parts) grow(p.cx, p.cy, p.w / 2, p.h / 2)
for (const c of copper) grow(c.x, c.y, c.r, c.r)

const margin = Number(values.margin) + profile.min_copper_edge_clearance
const roundTo = Number(values.round)
const need = {
  w: Math.ceil(((hi.x - lo.x) + 2 * margin) / roundTo) * roundTo,
  h: Math.ceil(((hi.y - lo.y) + 2 * margin) / roundTo) * roundTo,
}
// The content is rarely centred on the origin, so a shrunk board needs its parts moved
// as a group — reported rather than silently applied, because moving parts is a design
// change and the outline is not.
const centre = { x: (lo.x + hi.x) / 2, y: (lo.y + hi.y) / 2 }

const current = { w: board.width, h: board.height }
const areaNow = current.w * current.h
const areaNeed = need.w * need.h
const saving = 1 - areaNeed / areaNow

const fmt = (n: number) => n.toFixed(2)
console.log(`board        ${fmt(current.w)} x ${fmt(current.h)} mm   (${fmt(areaNow)} mm²)`)
console.log(`content      ${fmt(hi.x - lo.x)} x ${fmt(hi.y - lo.y)} mm, centred at (${fmt(centre.x)}, ${fmt(centre.y)})`)
console.log(
  `margin       ${values.margin} mm handling + ${profile.min_copper_edge_clearance} mm copper-to-edge ` +
    `(from "${profile.name}")`,
)
console.log(`suggested    ${fmt(need.w)} x ${fmt(need.h)} mm   (${fmt(areaNeed)} mm²)`)

if (saving > 0.02) {
  console.log(`\n  ${(saving * 100).toFixed(0)}% smaller — ${fmt(areaNow - areaNeed)} mm² of board doing nothing`)
} else if (saving < -0.001) {
  console.log(
    `\n  the outline is ${fmt(areaNeed - areaNow)} mm² TOO SMALL — content does not fit inside it`,
  )
} else {
  console.log(`\n  already about right`)
}

// Anything outside the current outline is a defect regardless of size, and the reason
// to look at this before shrinking rather than after.
const off = parts.filter((p) => p.nearestGap < 0)
if (off.length) {
  console.log(
    `\n  ${off.length} part(s) already outside the outline: ` +
      off.map((p) => `${p.ref} (${fmt(-p.nearestGap)} mm)`).join(", "),
  )
}
if (Math.abs(centre.x) > 0.5 || Math.abs(centre.y) > 0.5) {
  console.log(
    `\n  content is off-centre by (${fmt(centre.x)}, ${fmt(centre.y)}) mm — shrinking to the` +
      ` suggested outline needs the parts shifted by that much, or the saving is not real`,
  )
}

if (values.apply) {
  const file = path.resolve(values.apply)
  let src = await fs.readFile(file, "utf8")
  const before = src
  src = src.replace(/(<board[^>]*?\bwidth=")([^"]+)(")/, `$1${need.w}mm$3`)
  src = src.replace(/(<board[^>]*?\bheight=")([^"]+)(")/, `$1${need.h}mm$3`)
  if (src === before) {
    console.error(`\ncould not find width/height on the <board> element in ${values.apply}`)
    process.exit(1)
  }
  await fs.writeFile(file, src)
  console.log(`\napplied to ${values.apply} — recompile to confirm it still routes`)
}
