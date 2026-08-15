#!/usr/bin/env tsx
/**
 * A browsable front end for everything the pipeline has produced.
 *
 *   npx tsx tools/serve-ui.ts --port 8500
 *
 * The per-run viewer already existed as a single self-contained file, which is ideal for
 * copying or attaching and awkward for actually working: a rover is several boards, and
 * comparing them meant opening several files and remembering which was which.
 *
 * So this serves an index across every run — gate status, part counts, when it ran — and
 * the viewer for each. Viewers are generated on demand and cached, because building one
 * costs a GLB export and an esbuild bundle, and a board that has not changed does not
 * need either again.
 *
 * Deliberately read-only. It shows what the pipeline decided; it cannot re-run anything,
 * so leaving it open cannot start work or consume the GPU.
 */
import http from "node:http"
import fs from "node:fs/promises"
import path from "node:path"
import { execFile } from "node:child_process"
import { parseArgs } from "node:util"
import { promisify } from "node:util"

const run = promisify(execFile)

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8500" },
    root: { type: "string", default: "runs" },
    host: { type: "string", default: "0.0.0.0" },
  },
})

const PORT = Number(values.port)
const ROOT = path.resolve(values.root!)
const MANIFEST = path.resolve("robots.json")

/**
 * Which robot each board belongs to.
 *
 * A manifest rather than a naming convention, because a robot's boards do not reliably
 * share a prefix — the rover's controller run is called `regress`. Unlisted runs fall
 * back to prefix grouping and then to "unassigned", so a new run always appears
 * somewhere rather than vanishing because nobody edited a file.
 */
interface RobotBoard { run: string; role?: string; count?: number; note?: string }
interface Robot { id: string; name: string; description?: string; boards: RobotBoard[] }

async function loadRobots(): Promise<Robot[]> {
  try {
    const parsed = JSON.parse(await fs.readFile(MANIFEST, "utf8"))
    return Array.isArray(parsed.robots) ? parsed.robots : []
  } catch {
    return []
  }
}

interface BoardSummary {
  name: string
  dir: string
  mtime: number
  parts?: number
  nets?: number
  peakC?: number
  dfm?: { errors: number; warnings: number }
  spice?: string
  placement?: string
  verdict?: "PASS" | "REVISE"
  hasViewer: boolean
  gates: Array<{ label: string; state: "pass" | "fail" | "warn" | "skip"; detail: string }>
}

const exists = async (p: string) => {
  try {
    await fs.access(p)
    return true
  } catch {
    return false
  }
}

const readOr = async (f: string, fallback = "") =>
  (await exists(f)) ? await fs.readFile(f, "utf8") : fallback

/** Latest iter-N in a run directory. */
async function latestIter(dir: string): Promise<string | null> {
  try {
    const entries = await fs.readdir(dir)
    const iters = entries
      .filter((e) => /^iter-\d+$/.test(e))
      .sort((a, b) => Number(a.slice(5)) - Number(b.slice(5)))
    return iters.length ? path.join(dir, iters[iters.length - 1]) : null
  } catch {
    return null
  }
}

/**
 * Read a run's status from the artifacts it already wrote.
 *
 * Parsed rather than recomputed, deliberately: the front end must show what the pipeline
 * decided. Recomputing here would let the page and the run disagree, and the page would
 * be the more visible of the two.
 */
async function summarise(name: string): Promise<BoardSummary | null> {
  const dir = path.join(ROOT, name)
  const iter = await latestIter(dir)
  if (!iter) return null

  const stat = await fs.stat(dir)
  const gates: BoardSummary["gates"] = []
  const s: BoardSummary = {
    name,
    dir,
    mtime: stat.mtimeMs,
    hasViewer: await exists(path.join(dir, "viewer.html")),
    gates,
  }

  const summaryText = await readOr(path.join(dir, "summary.json"))
  try {
    const parsed = JSON.parse(summaryText || "{}")
    const last = (parsed.iterations ?? []).at(-1)
    if (last) {
      s.parts = last.components
      s.nets = last.nets
      s.peakC = last.peak_temperature_c
      s.verdict = last.pass ? "PASS" : "REVISE"
    }
  } catch {
    /* a run killed mid-flight has no summary; the gate strip still works */
  }

  const lint = await readOr(path.join(iter, "lint.txt"))
  if (lint.trim()) {
    gates.push({
      label: "L0 lint",
      state: /clean/i.test(lint) ? "pass" : "fail",
      detail: /clean/i.test(lint) ? "clean" : lint.split("\n")[0],
    })
  }
  const report = await readOr(path.join(iter, "report.txt"))
  const partsMatch = report.match(/Components \((\d+)\)/)
  const netsMatch = report.match(/Nets \((\d+)\)/)
  if (partsMatch) s.parts ??= Number(partsMatch[1])
  if (netsMatch) s.nets ??= Number(netsMatch[1])

  const physics = await readOr(path.join(iter, "physics.txt"))
  const peak = physics.match(/peak[^0-9]*([0-9.]+)\s*°?C/i)
  if (peak) {
    s.peakC ??= Number(peak[1])
    gates.push({ label: "L6 physics", state: "pass", detail: `peak ${peak[1]} °C` })
  }

  const spice = await readOr(path.join(iter, "spice.txt"))
  if (spice.trim()) {
    const notRun = /NOT RUN/.test(spice)
    const fails = (spice.match(/^\s*FAIL\s/gm) ?? []).length
    const passes = (spice.match(/^\s*PASS\s/gm) ?? []).length
    s.spice = notRun ? "not run" : `${passes}/${passes + fails}`
    gates.push({
      label: "L7 simulation",
      state: notRun ? "skip" : fails ? "fail" : "pass",
      detail: notRun ? "ngspice absent" : `${passes}/${passes + fails} claims`,
    })
  }

  const dfm = await readOr(path.join(iter, "dfm.txt"))
  const dfmMatch = dfm.match(/(\d+) error\(s\), (\d+) warning\(s\)/)
  if (dfmMatch) {
    const errors = Number(dfmMatch[1])
    const warnings = Number(dfmMatch[2])
    s.dfm = { errors, warnings }
    gates.push({
      label: "L8 fab",
      state: errors ? "fail" : warnings ? "warn" : "pass",
      detail: `${errors} error${errors === 1 ? "" : "s"}, ${warnings} warning${warnings === 1 ? "" : "s"}`,
    })
  }

  const placement = await readOr(path.join(iter, "placement.txt"))
  if (placement.trim()) {
    const m = placement.match(/(\d+) rule\(s\) checked, (\d+) violation/)
    const unchecked = /no machine-checkable rules/.test(placement)
    s.placement = unchecked ? "unchecked" : m ? `${m[2]} violations` : "?"
    gates.push({
      label: "L3′ placement",
      state: unchecked ? "skip" : m && Number(m[2]) ? "fail" : "pass",
      detail: unchecked ? "no rules emitted" : m ? `${m[1]} rules, ${m[2]} violations` : "—",
    })
  }

  return s
}

async function listBoards(): Promise<BoardSummary[]> {
  let names: string[] = []
  try {
    names = (await fs.readdir(ROOT, { withFileTypes: true }))
      .filter((e) => e.isDirectory() && !e.name.startsWith("."))
      .map((e) => e.name)
  } catch {
    return []
  }
  const all = await Promise.all(names.map(summarise))
  return all.filter((b): b is BoardSummary => b !== null).sort((a, b) => b.mtime - a.mtime)
}

/** Build a viewer if it is missing or older than the run's artifacts. */
async function ensureViewer(name: string): Promise<string | null> {
  const dir = path.join(ROOT, name)
  const viewer = path.join(dir, "viewer.html")
  const iter = await latestIter(dir)
  if (!iter) return null

  if (await exists(viewer)) {
    const [v, c] = await Promise.all([
      fs.stat(viewer),
      fs.stat(path.join(iter, "circuit.json")).catch(() => null),
    ])
    if (!c || v.mtimeMs >= c.mtimeMs) return viewer
  }
  try {
    await run("npx", ["tsx", "tools/make-viewer.ts", dir], {
      cwd: path.resolve("."),
      timeout: 600_000,
      maxBuffer: 32 * 1024 * 1024,
    })
  } catch (err) {
    console.error(`  ! could not build viewer for ${name}: ${(err as Error).message.split("\n")[0]}`)
    return null
  }
  return (await exists(viewer)) ? viewer : null
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

function indexPage(boards: BoardSummary[], robots: Robot[]): string {
  const chip = (g: BoardSummary["gates"][number]) =>
    `<span class="chip ${g.state}" title="${esc(g.detail)}">${esc(g.label)}</span>`

  const byName = new Map(boards.map((b) => [b.name, b]))
  const claimed = new Set<string>()

  const card = (b: BoardSummary, meta?: RobotBoard) => {
    const when = new Date(b.mtime).toLocaleString()
    return `<a class="card" href="/board/${encodeURIComponent(b.name)}">
      <div class="card-head">
        <h2>${esc(meta?.role ?? b.name)}${meta && (meta.count ?? 1) > 1 ? `<span class="mult"> x${meta.count}</span>` : ""}</h2>
        ${b.verdict ? `<span class="verdict ${b.verdict.toLowerCase()}">${b.verdict}</span>` : ""}
      </div>
      ${meta?.role ? `<div class="runname">${esc(b.name)}</div>` : ""}
      ${meta?.note ? `<div class="note">${esc(meta.note)}</div>` : ""}
      <div class="meta">
        ${b.parts != null ? `${b.parts} parts` : "—"} ·
        ${b.nets != null ? `${b.nets} nets` : "—"}
        ${b.peakC != null ? ` · peak ${b.peakC.toFixed(1)} °C` : ""}
      </div>
      <div class="chips">${b.gates.map(chip).join("")}</div>
      <div class="when">${esc(when)}</div>
    </a>`
  }

  const sections: string[] = []
  for (const robot of robots) {
    const cards = robot.boards
      .map((rb) => {
        const b = byName.get(rb.run)
        if (!b) return ""
        claimed.add(rb.run)
        return card(b, rb)
      })
      .filter(Boolean)
    if (!cards.length) continue
    // A robot is only as built as its worst board, so the header says that outright
    // rather than leaving it to be inferred from a row of chips.
    const its = robot.boards.map((rb) => byName.get(rb.run)).filter(Boolean) as BoardSummary[]
    const failing = its.filter((b) => b.gates.some((g) => g.state === "fail")).length
    const fitted = robot.boards.reduce((n, rb) => n + (byName.has(rb.run) ? (rb.count ?? 1) : 0), 0)
    sections.push(`<section class="robot">
      <div class="robot-head">
        <h2 class="robot-name">${esc(robot.name)}</h2>
        <span class="robot-stat ${failing ? "bad" : "good"}">${
          failing ? `${failing} of ${its.length} board(s) failing a gate` : `all ${its.length} boards clean`
        }</span>
      </div>
      ${robot.description ? `<p class="robot-desc">${esc(robot.description)} · ${fitted} board${fitted === 1 ? "" : "s"} fitted</p>` : ""}
      <div class="grid">${cards.join("")}</div>
    </section>`)
  }

  const rest = boards.filter((b) => !claimed.has(b.name))
  if (rest.length) {
    sections.push(`<section class="robot">
      <div class="robot-head"><h2 class="robot-name">Unassigned</h2>
      <span class="robot-stat">${rest.length} run${rest.length === 1 ? "" : "s"}</span></div>
      <p class="robot-desc">Not listed in <code>robots.json</code> — add them there to group them under a robot.</p>
      <div class="grid">${rest.map((b) => card(b)).join("")}</div>
    </section>`)
  }

  const rows = sections.join("\n")

  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pcb-ai — boards</title>
<style>
  :root{color-scheme:light dark;--ground:#f7f6f2;--panel:#fffefb;--ink:#191b18;--muted:#6d7169;
        --line:#e0e0d6;--accent:#a35a1e;--pass:#2f6b45;--warn:#8a6412;--fail:#a32c25;
        --pass-bg:#e4efe7;--warn-bg:#f6eed8;--fail-bg:#f7e3e1;--skip-bg:#ecece6}
  @media (prefers-color-scheme:dark){:root:not([data-theme=light]){
        --ground:#14150f;--panel:#1c1e17;--ink:#e9e9e0;--muted:#969a8c;--line:#2f3228;
        --accent:#d99a5c;--pass:#7fc79b;--warn:#e0be6a;--fail:#e8897f;
        --pass-bg:#1e2f24;--warn-bg:#2f2818;--fail-bg:#33201e;--skip-bg:#23241d}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--ground);color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui,sans-serif;
       font-variant-numeric:tabular-nums}
  .wrap{max-width:1200px;margin:0 auto;padding:28px 24px 64px}
  .eyebrow{font:600 11px/1 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;
           color:var(--accent);margin:0 0 8px}
  h1{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
  .sub{color:var(--muted);font-size:13.5px;margin:0 0 24px}
  .grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(330px,1fr))}
  .card{display:block;text-decoration:none;color:inherit;background:var(--panel);
        border:1px solid var(--line);border-radius:10px;padding:15px;transition:border-color .12s}
  .card:hover{border-color:var(--accent)}
  .card-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
  h2{font-size:15.5px;margin:0 0 3px;letter-spacing:-.01em;word-break:break-word}
  .verdict{font-size:11px;font-weight:650;padding:2px 8px;border-radius:999px;white-space:nowrap}
  .verdict.pass{background:var(--pass-bg);color:var(--pass)}
  .verdict.revise{background:var(--fail-bg);color:var(--fail)}
  .meta{color:var(--muted);font-size:12.5px;margin:2px 0 10px;
        font-family:ui-monospace,monospace}
  .chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:9px}
  .chip{font-size:11px;padding:2px 7px;border-radius:5px;background:var(--skip-bg);color:var(--muted)}
  .chip.pass{background:var(--pass-bg);color:var(--pass)}
  .chip.fail{background:var(--fail-bg);color:var(--fail)}
  .chip.warn{background:var(--warn-bg);color:var(--warn)}
  .when{color:var(--muted);font-size:11.5px;font-family:ui-monospace,monospace}
  .empty{color:var(--muted);padding:40px 0}
  .robot{margin:0 0 34px}
  .robot-head{display:flex;align-items:baseline;gap:12px;
              border-bottom:1px solid var(--line);padding-bottom:7px;margin-bottom:6px}
  .robot-name{font-size:17px;margin:0;letter-spacing:-.01em}
  .robot-stat{font-size:11.5px;font-weight:650;padding:2px 8px;border-radius:999px;
              background:var(--skip-bg);color:var(--muted);white-space:nowrap}
  .robot-stat.good{background:var(--pass-bg);color:var(--pass)}
  .robot-stat.bad{background:var(--warn-bg);color:var(--warn)}
  .robot-desc{color:var(--muted);font-size:12.5px;margin:0 0 13px}
  .runname{font-family:ui-monospace,monospace;font-size:11.5px;color:var(--muted);margin-bottom:3px}
  .note{font-size:12px;color:var(--muted);margin-bottom:7px;line-height:1.4}
  .mult{color:var(--accent);font-weight:650}
</style></head><body><div class="wrap">
  <p class="eyebrow">pcb-ai</p>
  <h1>Boards</h1>
  <p class="sub">${boards.length} run${boards.length === 1 ? "" : "s"} under <code>${esc(ROOT)}</code> · grouped by robot from <code>robots.json</code> · click any board for schematic, PCB, 3D and every gate report</p>
  ${boards.length ? rows : '<p class="empty">No runs yet.</p>'}
</div></body></html>`
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`)
  const send = (code: number, type: string, body: string | Buffer) => {
    res.writeHead(code, { "content-type": type, "cache-control": "no-store" })
    res.end(body)
  }

  try {
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const [boards, robots] = await Promise.all([listBoards(), loadRobots()])
      return send(200, "text/html; charset=utf-8", indexPage(boards, robots))
    }

    const board = url.pathname.match(/^\/board\/([^/]+)\/?$/)
    if (board) {
      const name = decodeURIComponent(board[1])
      const viewer = await ensureViewer(name)
      if (!viewer) {
        return send(
          404,
          "text/html; charset=utf-8",
          `<p>No viewer could be built for <b>${esc(name)}</b>. It may have no compiled circuit.json — a run killed before L1 has nothing to show.</p><p><a href="/">back</a></p>`,
        )
      }
      return send(200, "text/html; charset=utf-8", await fs.readFile(viewer))
    }

    const file = url.pathname.match(/^\/board\/([^/]+)\/file\/(.+)$/)
    if (file) {
      // Resolved and then checked against the run directory: a path from a URL must
      // never be able to walk out of it.
      const name = decodeURIComponent(file[1])
      const rel = decodeURIComponent(file[2])
      const base = path.resolve(ROOT, name)
      const target = path.resolve(base, rel)
      if (!target.startsWith(base + path.sep)) return send(403, "text/plain", "outside the run")
      if (!(await exists(target))) return send(404, "text/plain", "not found")
      const types: Record<string, string> = {
        ".json": "application/json",
        ".glb": "model/gltf-binary",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
      }
      return send(200, types[path.extname(target)] ?? "application/octet-stream", await fs.readFile(target))
    }

    send(404, "text/plain", `no route for ${url.pathname}`)
  } catch (err) {
    send(500, "text/plain", (err as Error).message)
  }
})

server.listen(PORT, values.host!, async () => {
  const boards = await listBoards()
  console.log(`pcb-ai front end on http://localhost:${PORT}`)
  console.log(`  serving ${boards.length} run(s) from ${ROOT}`)
  console.log(`  bound to ${values.host} — reachable from other machines on the LAN`)
})
