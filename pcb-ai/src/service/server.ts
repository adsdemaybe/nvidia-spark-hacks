/**
 * The PCB service — pcb-ai over HTTP.
 *
 * `cad-generation/api` exposes the CAD half at `/cad/…` and `src/cad/client.ts` already
 * calls it. Nothing called *us*: the board loop was a CLI, so F2's enclosure designer,
 * F3's scene builder and F5's twin had no way to reach it from Python. This is the other
 * end of that wire, namespaced `/pcb/…` to mirror theirs.
 *
 * Deliberately dependency-free — `node:http` and the handlers the CLI already uses. A
 * framework would buy routing sugar and a supply-chain question, and there are nine
 * endpoints.
 *
 * Two design rules worth stating:
 *
 *   - **Every long operation returns a job id.** Compiling and routing a board is tens
 *     of seconds and a design loop is minutes; a caller that blocks on it will time out
 *     somewhere less useful than here.
 *   - **The service reports; it never adjudicates.** `check_fit` returns measured
 *     violations, `replace_within` returns a re-placed board *or the reason there isn't
 *     one*. Nothing here decides that a board is acceptable — that is the ladder's job,
 *     and the negotiator's.
 *
 *   npx tsx src/service/server.ts --port 8300
 */
import http from "node:http"
import fs from "node:fs/promises"
import path from "node:path"
import { parseArgs } from "node:util"
import { build } from "../build.ts"
import { deriveBoardReport, describeAssumptions, riskyAssumptions } from "../cad/board-report.ts"
import { BoardReport, Envelope, type EnclosureReport } from "../cad/contracts.ts"
import { runDfm, describeDfm } from "../dfm/index.ts"
import { loadKicadProProfile, DEFAULT_PROFILE, type FabProfile } from "../dfm/profile.ts"
import { checkPlacement, describePlacement } from "../placement/check.ts"
import { runSpice, describeSpice, type Claim } from "../spice/index.ts"
import { runPhysics, describePhysics } from "../physics/index.ts"
import { exportHandoff } from "../exports.ts"
import { replaceWithin } from "./replace-within.ts"
import type { OperatingPoint } from "../schemas.ts"

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8300" },
    root: { type: "string", default: "runs" },
    "fab-profile": { type: "string" },
  },
})

const PORT = Number(values.port)
const ROOT = path.resolve(values.root!)
let FAB: FabProfile = DEFAULT_PROFILE

/** In-flight and finished jobs. Deliberately in-memory: a restart should lose nothing
 *  that matters, because every artifact is on disk under its design id. */
interface Job {
  id: string
  state: "running" | "done" | "failed"
  started: number
  finished?: number
  stage?: string
  design_id?: string
  error?: string
  result?: unknown
}
const jobs = new Map<string, Job>()
let seq = 0
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}_${(seq++).toString(36)}`

const designDir = (id: string) => path.join(ROOT, "designs", id)

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8")) as T
  } catch {
    return null
  }
}

/** Compile HDL into a design directory and cache everything a later call may want. */
async function makeDesign(code: string, id: string) {
  const dir = designDir(id)
  await fs.mkdir(dir, { recursive: true })
  await fs.writeFile(path.join(dir, "circuit.tsx"), code, "utf8")

  const result = await build(code, dir)
  if (!result.ok) {
    throw new Error(result.compileError ?? "compile failed")
  }
  await fs.writeFile(
    path.join(dir, "circuit.json"),
    JSON.stringify(result.circuitJson, null, 2),
  )
  const derived = deriveBoardReport(result.circuitJson, { designId: id })
  await fs.writeFile(
    path.join(dir, "board_report.json"),
    JSON.stringify(derived.boardReport, null, 2),
  )
  await fs.writeFile(
    path.join(dir, "assumptions.json"),
    JSON.stringify(derived.assumptions, null, 2),
  )
  return { dir, result, derived }
}

/** Load a previously built design, or null. */
async function loadDesign(id: string) {
  const dir = designDir(id)
  const circuitJson = await readJson<any[]>(path.join(dir, "circuit.json"))
  if (!circuitJson) return null
  let code = ""
  try {
    code = await fs.readFile(path.join(dir, "circuit.tsx"), "utf8")
  } catch {
    /* a design registered from Circuit JSON alone has no HDL; replace_within will say so */
  }
  return { dir, circuitJson, code }
}

// ── routes ────────────────────────────────────────────────────────────────────

type Handler = (
  body: any,
  params: Record<string, string>,
) => Promise<{ status?: number; body: unknown } | { status: number; file: string }>

const routes: Array<{ method: string; pattern: RegExp; keys: string[]; handler: Handler }> = []

function route(method: string, spec: string, handler: Handler) {
  const keys: string[] = []
  const pattern = new RegExp(
    "^" +
      spec.replace(/:[a-zA-Z_]+/g, (m) => {
        keys.push(m.slice(1))
        return "([^/]+)"
      }) +
      "$",
  )
  routes.push({ method, pattern, keys, handler })
}

route("GET", "/health", async () => ({
  body: {
    ok: true,
    service: "pcb-ai",
    fab_profile: FAB.name,
    designs_root: ROOT,
    endpoints: routes.map((r) => `${r.method} ${r.pattern.source.slice(1, -1)}`),
  },
}))

/**
 * Register a design from HDL. Synchronous because a caller that hands us source
 * generally wants to know immediately whether it compiles.
 */
route("POST", "/pcb/designs", async (body) => {
  const code: string = body?.code ?? body?.hdl
  if (typeof code !== "string" || !code.includes("export default")) {
    return { status: 400, body: { error: "body.code must be tscircuit HDL with a default export" } }
  }
  const id = body?.design_id ?? newId("d")
  const { derived } = await makeDesign(code, id)
  return {
    status: 201,
    body: {
      design_id: id,
      board_report: derived.boardReport,
      risky_assumptions: riskyAssumptions(derived.assumptions).length,
    },
  }
})

route("GET", "/pcb/designs/:id/board_report", async (_b, p) => {
  const report = await readJson(path.join(designDir(p.id), "board_report.json"))
  return report
    ? { body: report }
    : { status: 404, body: { error: `no design ${p.id}` } }
})

route("GET", "/pcb/designs/:id/assumptions", async (_b, p) => {
  const a = await readJson<any[]>(path.join(designDir(p.id), "assumptions.json"))
  if (!a) return { status: 404, body: { error: `no design ${p.id}` } }
  return { body: { assumptions: a, risky: riskyAssumptions(a as any), text: describeAssumptions(a as any) } }
})

/**
 * `pcb.check_fit(enclosure_report)` — measured violations, from this side.
 *
 * The CAD service runs the same check from its side; §6 wants both, because two
 * independent geometry checks agreeing is signal and disagreeing is a pipeline bug.
 */
route("POST", "/pcb/designs/:id/check_fit", async (body, p) => {
  const design = await loadDesign(p.id)
  if (!design) return { status: 404, body: { error: `no design ${p.id}` } }
  const enclosure = body?.enclosure_report as EnclosureReport | undefined
  if (!enclosure) return { status: 400, body: { error: "body.enclosure_report is required" } }

  const derived = deriveBoardReport(design.circuitJson, { designId: p.id })
  const board = derived.boardReport
  const violations: Array<{ code: string; severity: string; detail: string }> = []

  const cavity = (enclosure as any).cavity_mm
  if (cavity) {
    const cw = Math.abs(cavity.max_x_mm - cavity.min_x_mm)
    const ch = Math.abs(cavity.max_y_mm - cavity.min_y_mm)
    // outline is a polygon, not a rectangle — take its bounding box, which is what a
    // rectangular cavity can actually accept.
    const pts = board.outline_mm?.points ?? []
    const bw = pts.length ? Math.max(...pts.map((q) => q.x_mm)) - Math.min(...pts.map((q) => q.x_mm)) : 0
    const bh = pts.length ? Math.max(...pts.map((q) => q.y_mm)) - Math.min(...pts.map((q) => q.y_mm)) : 0
    if (bw > cw + 1e-6 || bh > ch + 1e-6) {
      violations.push({
        code: "board_exceeds_cavity",
        severity: "blocker",
        detail: `board is ${bw.toFixed(2)}x${bh.toFixed(2)} mm, cavity is ${cw.toFixed(2)}x${ch.toFixed(2)} mm`,
      })
    }
  }

  const maxH = (enclosure as any).max_component_height_mm
  if (typeof maxH === "number") {
    for (const c of board.component_heightmap ?? []) {
      if ((c as any).height_mm > maxH + 1e-6) {
        violations.push({
          code: "component_too_tall",
          severity: "blocker",
          detail: `${(c as any).ref} is ${(c as any).height_mm.toFixed(2)} mm tall, lid allows ${maxH.toFixed(2)} mm`,
        })
      }
    }
  }

  return { body: { design_id: p.id, violations, checked_from: "pcb" } }
})

/** `pcb.replace_within(envelope)` — the other half of the §6 loop. */
route("POST", "/pcb/designs/:id/replace_within", async (body, p) => {
  const design = await loadDesign(p.id)
  if (!design) return { status: 404, body: { error: `no design ${p.id}` } }
  if (!design.code) {
    return {
      status: 409,
      body: {
        error:
          "this design was registered without HDL, so its outline cannot be constrained. " +
          "Register with POST /pcb/designs {code} to enable replace_within.",
      },
    }
  }
  const parsed = Envelope.safeParse(body?.envelope)
  if (!parsed.success) {
    return { status: 400, body: { error: "body.envelope is not a valid Envelope", detail: parsed.error.issues } }
  }

  const nextId = newId("d")
  const out = await replaceWithin({
    code: design.code,
    envelope: parsed.data,
    dir: designDir(nextId),
    fabProfile: FAB,
  })
  if (!out.ok) {
    // Not an HTTP error: "this envelope cannot be satisfied" is a legitimate,
    // well-formed answer, and the negotiator needs to read it as one.
    return { body: { ok: false, reason: out.reason, applied: out.applied } }
  }
  await fs.writeFile(
    path.join(designDir(nextId), "board_report.json"),
    JSON.stringify(out.board_report, null, 2),
  )
  return { body: { ok: true, design_id: nextId, board_report: out.board_report, applied: out.applied } }
})

/** Every deterministic report the ladder produces, on demand. */
route("POST", "/pcb/designs/:id/analyse", async (body, p) => {
  const design = await loadDesign(p.id)
  if (!design) return { status: 404, body: { error: `no design ${p.id}` } }

  const built = await build(design.code, design.dir)
  const op = body?.operating_point as OperatingPoint | undefined
  const claims = (body?.claims ?? []) as Claim[]

  const out: Record<string, unknown> = {}
  const dfm = runDfm(design.circuitJson, FAB)
  out.dfm = { errors: dfm.errors, warnings: dfm.warnings, text: describeDfm(dfm) }

  const placement = checkPlacement(design.circuitJson, body?.placement_rules ?? [])
  out.placement = {
    checked: placement.checked,
    violations: placement.violations.length,
    text: describePlacement(placement),
  }

  if (op) {
    const physics = await runPhysics(design.circuitJson, op, path.join(design.dir, "physics"))
    out.physics = { peak_c: physics.thermal?.peak_c, text: describePhysics(physics) }
    const spice = await runSpice({ build: built, operatingPoint: op, claims, dir: design.dir })
    out.spice = {
      available: spice.available,
      passing: spice.claims.filter((c) => c.pass).length,
      total: spice.claims.length,
      coverage_pct: Math.round(spice.coveragePercent),
      text: describeSpice(spice),
    }
  }
  return { body: { design_id: p.id, ...out } }
})

/** STEP/GLB/KiCad for CAD and sim; Gerbers for the fab. */
route("POST", "/pcb/designs/:id/artifacts", async (_b, p) => {
  const design = await loadDesign(p.id)
  if (!design) return { status: 404, body: { error: `no design ${p.id}` } }
  const results = await exportHandoff(design.circuitJson, path.join(design.dir, "handoff"), "board")
  return { body: { design_id: p.id, artifacts: results } }
})

route("GET", "/pcb/designs/:id/artifact/:name", async (_b, p) => {
  const file = path.join(designDir(p.id), "handoff", p.name)
  const nested = path.join(designDir(p.id), "handoff", "kicad", p.name)
  for (const f of [file, nested]) {
    try {
      await fs.access(f)
      return { status: 200, file: f }
    } catch {
      /* try the next */
    }
  }
  return { status: 404, body: { error: `no artifact ${p.name} for ${p.id}` } }
})

route("GET", "/pcb/jobs/:id", async (_b, p) => {
  const job = jobs.get(p.id)
  return job ? { body: job } : { status: 404, body: { error: `no job ${p.id}` } }
})

// ── plumbing ──────────────────────────────────────────────────────────────────

const MIME: Record<string, string> = {
  ".glb": "model/gltf-binary",
  ".json": "application/json",
  ".step": "model/step",
  ".kicad_pcb": "text/plain",
  ".kicad_sch": "text/plain",
  ".kicad_pro": "application/json",
}

const server = http.createServer((req, res) => {
  const chunks: Buffer[] = []
  req.on("data", (c) => chunks.push(c))
  req.on("end", async () => {
    const url = new URL(req.url ?? "/", `http://localhost:${PORT}`)
    const send = (status: number, payload: unknown) => {
      const text = JSON.stringify(payload, null, 2)
      res.writeHead(status, { "content-type": "application/json" })
      res.end(text)
    }

    for (const r of routes) {
      if (r.method !== (req.method ?? "GET")) continue
      const m = url.pathname.match(r.pattern)
      if (!m) continue

      const params: Record<string, string> = {}
      r.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1])))

      let body: any = undefined
      if (chunks.length) {
        try {
          body = JSON.parse(Buffer.concat(chunks).toString("utf8"))
        } catch (err) {
          return send(400, { error: `body is not JSON: ${(err as Error).message}` })
        }
      }

      try {
        const out = await r.handler(body, params)
        if ("file" in out) {
          const data = await fs.readFile(out.file)
          res.writeHead(200, {
            "content-type": MIME[path.extname(out.file)] ?? "application/octet-stream",
            "content-length": String(data.length),
          })
          return res.end(data)
        }
        return send(out.status ?? 200, out.body)
      } catch (err) {
        // The message is the useful part: a caller debugging a 500 needs the compile
        // error, not a stack trace it cannot act on.
        return send(500, { error: (err as Error).message })
      }
    }
    send(404, { error: `no route for ${req.method} ${url.pathname}` })
  })
})

if (values["fab-profile"]) FAB = await loadKicadProProfile(values["fab-profile"])
await fs.mkdir(path.join(ROOT, "designs"), { recursive: true })

server.listen(PORT, () => {
  console.log(`pcb-ai service on http://localhost:${PORT}`)
  console.log(`  designs under ${ROOT}/designs`)
  console.log(`  fab profile: ${FAB.name}`)
  for (const r of routes) console.log(`  ${r.method.padEnd(4)} ${r.pattern.source.slice(1, -1)}`)
})
