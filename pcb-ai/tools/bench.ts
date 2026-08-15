#!/usr/bin/env tsx
/**
 * Benchmark harness — the plan §7.4 scorecard, made runnable.
 *
 * A bake-off is only worth anything if both sides face identical inputs and identical
 * gates. So this drives boards through the *same* ladder with only one variable changed
 * at a time, and reports the metrics the plan committed to rather than whatever looked
 * good afterwards.
 *
 * The variable is a "lane": a generator plus the model driving it. Path A lanes differ
 * only in the model. A path-B (SchGen) lane is declared here and reports `blocked` with
 * its reason until the bridge exists — an empty column that says *why* it is empty is
 * honest; quietly comparing one pipeline against nothing is not.
 *
 *   npx tsx tools/bench.ts --lanes stub,qwen --boards rover,blinker
 *   npx tsx tools/bench.ts --lanes stub --boards rover --out runs/bench
 */
import fs from "node:fs/promises"
import path from "node:path"
import { parseArgs } from "node:util"
import { build } from "../src/build.ts"
import { runPhysics, physicsBlockers } from "../src/physics/index.ts"
import { runSpice, type Claim } from "../src/spice/index.ts"
import { runDfm } from "../src/dfm/index.ts"
import { loadKicadProProfile, DEFAULT_PROFILE, type FabProfile } from "../src/dfm/profile.ts"
import { compareNetlists } from "../src/bench/netlist-similarity.ts"
import { lintHdl } from "../src/lint.ts"
import type { OperatingPoint } from "../src/schemas.ts"

const { values } = parseArgs({
  options: {
    boards: { type: "string", default: "rover" },
    lanes: { type: "string", default: "path-a" },
    out: { type: "string", default: "runs/bench" },
    "fab-profile": { type: "string" },
  },
})

interface BoardCase {
  name: string
  hdl: string
  operatingPoint?: string
  claims?: string
  /** A known-good netlist to score against. Defaults to the board itself, see below. */
  reference?: string
}

const BOARDS: Record<string, BoardCase> = {
  rover: {
    name: "rover",
    hdl: "examples/rover.tsx",
    operatingPoint: "examples/rover-op.json",
    claims: "examples/rover-claims.json",
  },
  "rover-packed": {
    name: "rover-packed",
    hdl: "examples/rover-packed.tsx",
    operatingPoint: "examples/rover-op.json",
    claims: "examples/rover-claims.json",
    // Scored against the hand-placed rover: same circuit, different placement. A
    // similarity below 1.0 here means the packer changed connectivity, which it must
    // never do — this is the one case where the metric is a genuine correctness check
    // rather than a regression signal.
    reference: "examples/rover.tsx",
  },
  blinker: {
    name: "blinker",
    hdl: "examples/blinker.tsx",
    operatingPoint: "examples/blinker-operating-point.json",
  },
  "blinker-1hz": {
    name: "blinker-1hz",
    hdl: "examples/blinker-1hz.tsx",
    operatingPoint: "examples/blinker-1hz-op.json",
    reference: "examples/blinker.tsx",
  },
}

interface Lane {
  id: string
  generator: "path-a" | "path-b"
  blocked?: string
}

const LANES: Record<string, Lane> = {
  "path-a": { id: "path-a", generator: "path-a" },
  // Path B cannot run here and the reason is specific, not a shrug: SchGen drives
  // KiCad 8's schematic API, `apt` on this aarch64 box offers only KiCad 7, and
  // installing anything system-wide needs a root password the agent does not have.
  // Plan §7.6 patch 4 and §7.7's kill criterion both hang off this.
  "path-b": {
    id: "path-b",
    generator: "path-b",
    blocked:
      "SchGen needs KiCad 8.0.9; this machine has no kicad-cli and apt offers only 7.0.11 (root required). Netlist bridge not built — plan §7.2/§7.6.",
  },
}

interface Row {
  board: string
  lane: string
  blocked?: string
  compiled?: boolean
  parts?: number
  nets?: number
  compileErrors?: number
  lintErrors?: number
  areaMm2?: number
  copperMm?: number
  vias?: number
  peakC?: number
  physicsBlockers?: number
  spiceClaims?: number
  spicePassing?: number
  spiceSubstantive?: number
  spiceCoverage?: number
  dfmErrors?: number
  dfmWarnings?: number
  netlistJaccard?: number
  netlistF1?: number
  wallMs?: number
  gateVerdict?: "PASS" | "FAIL"
  failures?: string[]
}

const profile: FabProfile = values["fab-profile"]
  ? await loadKicadProProfile(values["fab-profile"])
  : DEFAULT_PROFILE

const boardIds = values.boards!.split(",").map((s) => s.trim()).filter(Boolean)
const laneIds = values.lanes!.split(",").map((s) => s.trim()).filter(Boolean)
const outDir = values.out!
await fs.mkdir(outDir, { recursive: true })

const readJson = async <T,>(f?: string): Promise<T | undefined> =>
  f ? (JSON.parse(await fs.readFile(f, "utf8")) as T) : undefined

const rows: Row[] = []

for (const boardId of boardIds) {
  const board = BOARDS[boardId]
  if (!board) {
    console.error(`unknown board "${boardId}" — known: ${Object.keys(BOARDS).join(", ")}`)
    continue
  }

  for (const laneId of laneIds) {
    const lane = LANES[laneId] ?? { id: laneId, generator: "path-a" as const }
    if (lane.blocked) {
      rows.push({ board: board.name, lane: lane.id, blocked: lane.blocked })
      console.log(`${board.name} / ${lane.id}: BLOCKED — ${lane.blocked}`)
      continue
    }

    const workDir = path.join(outDir, `${board.name}__${lane.id}`)
    await fs.mkdir(workDir, { recursive: true })
    process.stdout.write(`${board.name} / ${lane.id}: `)
    const started = Date.now()

    const row: Row = { board: board.name, lane: lane.id }
    const failures: string[] = []

    try {
      const code = await fs.readFile(board.hdl, "utf8")

      // L0
      const lint = await lintHdl(code)
      const lintErrors = lint.filter((f) => f.severity === "error").length
      row.lintErrors = lintErrors
      if (lintErrors) failures.push(`L0: ${lintErrors} lint error(s)`)

      // L1
      const result = await build(code, workDir)
      row.compiled = result.ok
      row.parts = result.components.length
      row.nets = result.netlist.length
      row.compileErrors = result.findings.filter((f) => f.severity === "error").length
      if (!result.ok) {
        failures.push(`L1: ${result.compileError ?? "compile failed"}`)
        row.wallMs = Date.now() - started
        row.gateVerdict = "FAIL"
        row.failures = failures
        rows.push(row)
        console.log("compile FAILED")
        continue
      }
      if (row.compileErrors) failures.push(`L1: ${row.compileErrors} compiler error(s)`)

      if (result.board) row.areaMm2 = result.board.width * result.board.height

      // Routing volume, straight off the compiled geometry.
      let copper = 0
      for (const e of result.circuitJson as any[]) {
        if (e?.type !== "pcb_trace") continue
        const route = (e.route ?? []).filter((r: any) => typeof r.x === "number")
        for (let i = 1; i < route.length; i++) {
          copper += Math.hypot(route[i].x - route[i - 1].x, route[i].y - route[i - 1].y)
        }
      }
      row.copperMm = Math.round(copper * 10) / 10
      row.vias = (result.circuitJson as any[]).filter((e) => e?.type === "pcb_via").length

      // L6
      const op = await readJson<OperatingPoint>(board.operatingPoint)
      if (op) {
        const physics = await runPhysics(result.circuitJson, op, path.join(workDir, "physics"))
        row.peakC = physics.thermal?.peak_c
        const blockers = physicsBlockers(physics)
        row.physicsBlockers = blockers.length
        if (blockers.length) failures.push(`L6: ${blockers.length} hard failure(s)`)

        // L7
        const claims = (await readJson<Claim[]>(board.claims)) ?? []
        const spice = await runSpice({
          build: result,
          operatingPoint: op,
          claims,
          dir: workDir,
        })
        row.spiceClaims = spice.claims.length
        row.spicePassing = spice.claims.filter((c) => c.pass).length
        row.spiceSubstantive = spice.claims.filter((c) => !c.tautological).length
        row.spiceCoverage = Math.round(spice.coveragePercent)
        if (spice.available && spice.hardFailures.length) {
          failures.push(`L7: ${spice.hardFailures.length} hard failure(s)`)
        }
      }

      // L8
      const dfm = runDfm(result.circuitJson, profile)
      row.dfmErrors = dfm.errors
      row.dfmWarnings = dfm.warnings
      if (dfm.errors) failures.push(`L8: ${dfm.errors} fab violation(s)`)

      // Netlist similarity, where a reference exists.
      if (board.reference) {
        const refBuild = await build(
          await fs.readFile(board.reference, "utf8"),
          path.join(workDir, "reference"),
        )
        if (refBuild.ok) {
          const sim = compareNetlists(result.netlist, refBuild.netlist)
          row.netlistJaccard = Math.round(sim.jaccard * 1000) / 1000
          row.netlistF1 = Math.round(sim.f1 * 1000) / 1000
        }
      }
    } catch (err) {
      failures.push(`harness error: ${(err as Error).message.split("\n")[0]}`)
    }

    row.wallMs = Date.now() - started
    row.failures = failures
    row.gateVerdict = failures.length ? "FAIL" : "PASS"
    rows.push(row)
    console.log(
      `${row.gateVerdict} (${((row.wallMs ?? 0) / 1000).toFixed(1)}s)` +
        (failures.length ? ` — ${failures.join("; ")}` : ""),
    )
  }
}

const jsonPath = path.join(outDir, "scorecard.json")
await fs.writeFile(
  jsonPath,
  JSON.stringify({ profile: profile.name, rows }, null, 2),
)

// Markdown, because a scorecard nobody reads is not a scorecard.
const cols: Array<[string, (r: Row) => string]> = [
  ["board", (r) => r.board],
  ["lane", (r) => r.lane],
  ["gate", (r) => r.blocked ? "BLOCKED" : (r.gateVerdict ?? "—")],
  ["parts", (r) => String(r.parts ?? "—")],
  ["nets", (r) => String(r.nets ?? "—")],
  ["area mm²", (r) => (r.areaMm2 != null ? r.areaMm2.toFixed(0) : "—")],
  ["copper mm", (r) => (r.copperMm != null ? r.copperMm.toFixed(0) : "—")],
  ["vias", (r) => String(r.vias ?? "—")],
  ["peak °C", (r) => (r.peakC != null ? r.peakC.toFixed(1) : "—")],
  ["L7 pass", (r) => (r.spiceClaims != null ? `${r.spicePassing}/${r.spiceClaims} (${r.spiceSubstantive} real)` : "—")],
  ["L7 cov", (r) => (r.spiceCoverage != null ? `${r.spiceCoverage}%` : "—")],
  ["L8 err/warn", (r) => (r.dfmErrors != null ? `${r.dfmErrors}/${r.dfmWarnings}` : "—")],
  ["netlist J", (r) => (r.netlistJaccard != null ? r.netlistJaccard.toFixed(3) : "—")],
  ["wall s", (r) => (r.wallMs != null ? (r.wallMs / 1000).toFixed(1) : "—")],
]

const md = [
  `# Scorecard — fab profile \`${profile.name}\``,
  "",
  `| ${cols.map((c) => c[0]).join(" | ")} |`,
  `|${cols.map(() => "---").join("|")}|`,
  ...rows.map((r) => `| ${cols.map((c) => c[1](r)).join(" | ")} |`),
  "",
  "## Failures",
  "",
  ...rows.flatMap((r) =>
    r.blocked
      ? [`- **${r.board} / ${r.lane}** — BLOCKED: ${r.blocked}`]
      : (r.failures ?? []).length
        ? [`- **${r.board} / ${r.lane}**: ${(r.failures ?? []).join("; ")}`]
        : [],
  ),
  rows.every((r) => r.blocked || !(r.failures ?? []).length) ? "- none" : "",
].join("\n")

const mdPath = path.join(outDir, "scorecard.md")
await fs.writeFile(mdPath, md)

console.log()
console.log(md)
console.log()
console.log(`scorecard → ${mdPath} and ${jsonPath}`)
