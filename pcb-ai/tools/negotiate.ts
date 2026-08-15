#!/usr/bin/env tsx
/**
 * Drive the §6 PCB↔CAD negotiation, both halves live.
 *
 *   # bring the CAD side up first (see cad-generation/api)
 *   npx tsx tools/negotiate.ts examples/rover-fixed.tsx --cad http://127.0.0.1:8400
 *
 * The loop: design the enclosure around the board, both sides check fit, and if either
 * reports a blocking violation the side with more freedom moves — the board re-places
 * inside the envelope CAD will accept. Hard stop at three rounds; a non-converged pair
 * is reported as such and never papered over.
 *
 * `replaceWithin` is supplied here rather than by the client, because re-placing means
 * rewriting the outline and rebuilding, and only this side can do that. Until it
 * existed, `negotiate()` ran one round and reported non-convergence honestly — a
 * handshake rather than a negotiation.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { parseArgs } from "node:util"
import { build } from "../src/build.ts"
import { CadClient, negotiate, describeNegotiation } from "../src/cad/client.ts"
import { deriveBoardReport, riskyAssumptions } from "../src/cad/board-report.ts"
import { replaceWithin } from "../src/service/replace-within.ts"
import { loadKicadProProfile, DEFAULT_PROFILE } from "../src/dfm/profile.ts"

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    cad: { type: "string", default: "http://127.0.0.1:8400" },
    out: { type: "string", default: "runs/negotiate" },
    rounds: { type: "string", default: "3" },
    "fab-profile": { type: "string" },
    artifacts: { type: "boolean", default: false },
  },
})

const hdlPath = positionals[0]
if (!hdlPath) {
  console.error("usage: negotiate.ts <board.tsx> [--cad URL] [--rounds N] [--artifacts]")
  process.exit(1)
}

const fabProfile = values["fab-profile"]
  ? await loadKicadProProfile(values["fab-profile"])
  : DEFAULT_PROFILE

const outDir = values.out!
await fs.mkdir(outDir, { recursive: true })

let code = await fs.readFile(hdlPath, "utf8")
process.stdout.write("compiling the board … ")
const built = await build(code, path.join(outDir, "round-0"))
if (!built.ok) {
  console.log("FAILED")
  console.error(built.compileError ?? "compile failed")
  process.exit(1)
}
const { boardReport, assumptions } = deriveBoardReport(built.circuitJson, { designId: "negotiate" })
console.log(`${built.components.length} parts`)

const risky = riskyAssumptions(assumptions)
if (risky.length) {
  // The enclosure is designed against these numbers. An ASSUMED component height that
  // is wrong produces a lid that fits in the report and not on the bench.
  console.log(`  ${risky.length} risky assumption(s) feeding the enclosure — see assumptions.json`)
  await fs.writeFile(
    path.join(outDir, "assumptions.json"),
    JSON.stringify(assumptions, null, 2),
  )
}

const cad = new CadClient({ baseUrl: values.cad! })
try {
  const health = await cad.health()
  console.log(`cad       ${values.cad} — generators: ${health.generators.join(", ")}`)
} catch (err) {
  console.error(
    `\ncannot reach the CAD service at ${values.cad}: ${(err as Error).message}\n` +
      `Start it with:  cd cad-generation && .venv/bin/python -m uvicorn cad_api.service:app --port 8400`,
  )
  process.exit(1)
}

let round = 0
const result = await negotiate(cad, boardReport, {
  maxRounds: Number(values.rounds),
  emitArtifacts: values.artifacts,
  replaceWithin: async (envelope) => {
    round++
    console.log(`\n  round ${round}: CAD constrains the board — ${envelope.reason}`)
    const out = await replaceWithin({
      code,
      envelope,
      dir: path.join(outDir, `round-${round}`),
      fabProfile,
    })
    if (!out.ok) {
      // Throwing is how the negotiator learns this envelope is unsatisfiable; it stops
      // and reports non-convergence rather than looping on an impossible constraint.
      throw new Error(out.reason ?? "the board cannot be re-placed inside this envelope")
    }
    code = out.code!
    console.log(`  round ${round}: PCB re-places — ${out.applied.join("; ")}`)
    return out.board_report!
  },
})

console.log()
console.log(describeNegotiation(result))

await fs.writeFile(path.join(outDir, "negotiation.json"), JSON.stringify(result, null, 2))
if (code !== (await fs.readFile(hdlPath, "utf8"))) {
  await fs.writeFile(path.join(outDir, "final.tsx"), code)
  console.log(`\nthe board changed — final HDL at ${path.join(outDir, "final.tsx")}`)
}
console.log(`negotiation record → ${path.join(outDir, "negotiation.json")}`)

process.exit(result.converged ? 0 : 1)
