#!/usr/bin/env tsx
/**
 * Turn a run directory into one self-contained HTML page.
 *
 * A run produces a dozen SVGs, a GLB, four text reports and a JSON verdict scattered
 * across `iter-N/` directories. Reviewing that means opening files one at a time in
 * different applications, which is why nobody does it and why problems visible in the
 * assembly render go unnoticed for three iterations.
 *
 * Everything is inlined — three.js bundled by esbuild, SVGs as markup, the GLB as
 * base64. The output has no network dependency at all, which matters twice over: the
 * Spark's outbound access is unreliable (the tscircuit model CDN already times out
 * mid-compile), and a single file can be copied to a laptop or published as-is.
 *
 *   npx tsx tools/make-viewer.ts runs/<dir> [-o viewer.html]
 */
import fs from "node:fs/promises"
import path from "node:path"
import { execFile } from "node:child_process"
import { parseArgs } from "node:util"
import { promisify } from "node:util"
import { exportGlb } from "../src/exports.ts"

const run = promisify(execFile)

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: "string", short: "o" },
    iteration: { type: "string" },
  },
})

const runDir = positionals[0]
if (!runDir) {
  console.error("usage: make-viewer.ts <run-dir> [-o out.html] [--iteration N]")
  process.exit(1)
}

const exists = async (p: string) => {
  try {
    await fs.access(p)
    return true
  } catch {
    return false
  }
}

/** Latest `iter-N` unless one was named. */
async function pickIteration(dir: string): Promise<string> {
  if (values.iteration) return path.join(dir, `iter-${values.iteration}`)
  const entries = await fs.readdir(dir)
  const iters = entries
    .filter((e) => /^iter-\d+$/.test(e))
    .sort((a, b) => Number(a.slice(5)) - Number(b.slice(5)))
  if (!iters.length) throw new Error(`no iter-N directories in ${dir}`)
  return path.join(dir, iters[iters.length - 1])
}

const iterDir = await pickIteration(runDir)
const iterName = path.basename(iterDir)

const readOr = async (file: string, fallback = "") =>
  (await exists(file)) ? await fs.readFile(file, "utf8") : fallback

/** Inline an SVG, stripped of the XML prolog so it can sit in the document directly. */
async function readSvg(file: string): Promise<string | null> {
  if (!(await exists(file))) return null
  const text = await fs.readFile(file, "utf8")
  return text.replace(/^<\?xml[^>]*\?>\s*/, "").replace(/<!DOCTYPE[^>]*>\s*/, "")
}

const views: Array<{ id: string; label: string; svg: string | null }> = [
  { id: "schematic", label: "Schematic", svg: await readSvg(path.join(iterDir, "schematic.svg")) },
  { id: "pcb", label: "PCB", svg: await readSvg(path.join(iterDir, "pcb.svg")) },
  { id: "assembly", label: "Assembly", svg: await readSvg(path.join(iterDir, "assembly.svg")) },
]

// Heatmaps, if the physics stage rendered any.
const physicsDir = path.join(iterDir, "physics")
const heatmaps: Array<{ label: string; svg: string }> = []
if (await exists(physicsDir)) {
  for (const f of (await fs.readdir(physicsDir)).sort()) {
    if (!f.endsWith(".svg")) continue
    const svg = await readSvg(path.join(physicsDir, f))
    if (svg) heatmaps.push({ label: f.replace(/\.svg$/, ""), svg })
  }
}

const reports: Array<{ label: string; text: string }> = []
for (const [label, file] of [
  ["Netlist & compile", "report.txt"],
  ["Lint (L0)", "lint.txt"],
  ["Physics (L6)", "physics.txt"],
  ["Circuit simulation (L7)", "spice.txt"],
] as const) {
  const text = await readOr(path.join(iterDir, file))
  if (text.trim()) reports.push({ label, text })
}

// The GLB is exported on demand: a run that predates the exporter still gets a viewer.
let glbBase64 = ""
const circuitJsonPath = path.join(iterDir, "circuit.json")
if (await exists(circuitJsonPath)) {
  const existing = path.join(runDir, "handoff", "board.glb")
  let glbPath = (await exists(existing)) ? existing : null
  if (!glbPath) {
    process.stdout.write("exporting GLB … ")
    const result = await exportGlb(
      JSON.parse(await fs.readFile(circuitJsonPath, "utf8")),
      path.join(runDir, "handoff"),
      "board",
    )
    console.log(result.error ? `failed: ${result.error}` : "ok")
    glbPath = result.file ?? null
  }
  if (glbPath && (await exists(glbPath))) {
    glbBase64 = (await fs.readFile(glbPath)).toString("base64")
  }
}

process.stdout.write("bundling three.js … ")
const bundlePath = path.join(runDir, ".viewer-bundle.js")
await run("node_modules/.bin/esbuild", [
  "tools/viewer/main.ts",
  "--bundle",
  "--format=iife",
  "--minify",
  "--platform=browser",
  `--outfile=${bundlePath}`,
])
const bundle = await fs.readFile(bundlePath, "utf8")
await fs.rm(bundlePath, { force: true })
console.log(`${(bundle.length / 1024).toFixed(0)} kB`)

const verdict = await readOr(path.join(iterDir, "verdict.json"))
const summary = await readOr(path.join(runDir, "summary.json"))

const escape = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

const tabs = [
  ...views.filter((v) => v.svg).map((v) => ({ id: v.id, label: v.label })),
  ...(glbBase64 ? [{ id: "three", label: "3D" }] : []),
  ...(heatmaps.length ? [{ id: "heatmaps", label: "Physics fields" }] : []),
  ...(reports.length ? [{ id: "reports", label: "Reports" }] : []),
]

// Gate summary, read from the artifacts the run already wrote. Parsed rather than
// recomputed: the viewer must show what the pipeline decided, not a second opinion that
// could disagree with it.
interface Gate { label: string; state: "pass" | "fail" | "warn" | "skip"; detail: string }
const gates: Gate[] = []

const lintText = await readOr(path.join(iterDir, "lint.txt"))
if (lintText.trim()) {
  const errs = (lintText.match(/\berror\b/gi) ?? []).length
  gates.push({
    label: "L0 lint",
    state: /clean/i.test(lintText) || errs === 0 ? "pass" : "fail",
    detail: /clean/i.test(lintText) ? "clean" : `${errs} error(s)`,
  })
}

const latest = (() => {
  try {
    const s = JSON.parse(summary || "{}")
    const iters = s.iterations ?? []
    return iters[iters.length - 1] ?? null
  } catch {
    return null
  }
})()

if (latest?.peak_temperature_c != null) {
  gates.push({
    label: "L6 physics",
    state: (latest.drc_errors ?? 0) > 0 ? "fail" : "pass",
    detail: `peak ${latest.peak_temperature_c.toFixed(1)} °C · IR ${(latest.max_ir_drop_mv ?? 0).toFixed(1)} mV`,
  })
}

const spiceText = await readOr(path.join(iterDir, "spice.txt"))
if (spiceText.trim()) {
  const notRun = /NOT RUN/.test(spiceText)
  const fails = (spiceText.match(/^\s*FAIL\s/gm) ?? []).length
  const passes = (spiceText.match(/^\s*PASS\s/gm) ?? []).length
  const real = spiceText.match(/of which (\d+) can actually fail/)
  const cov = spiceText.match(/model coverage (\d+)%/)
  gates.push({
    label: "L7 simulation",
    state: notRun ? "skip" : fails ? "fail" : "pass",
    detail: notRun
      ? "ngspice not installed"
      : `${passes}/${passes + fails} claims${real ? `, ${real[1]} able to fail` : ""}${cov ? ` · ${cov[1]}% modelled` : ""}`,
  })
}

const dfmText = await readOr(path.join(iterDir, "dfm.txt"))
if (dfmText.trim()) {
  const m = dfmText.match(/(\d+) error\(s\), (\d+) warning\(s\)/)
  const errs = m ? Number(m[1]) : 0
  const warns = m ? Number(m[2]) : 0
  const prof = dfmText.match(/profile "([^"]+)"/)
  gates.push({
    label: "L8 manufacturability",
    state: errs ? "fail" : warns ? "warn" : "pass",
    detail: `${errs} error${errs === 1 ? "" : "s"}, ${warns} warning${warns === 1 ? "" : "s"}${prof ? ` · ${prof[1]}` : ""}`,
  })
}

let verdictPass: boolean | null = null
let verdictSummary = ""
try {
  const v = JSON.parse(verdict || "{}")
  if (typeof v.pass === "boolean") verdictPass = v.pass
  verdictSummary = v.summary ?? ""
} catch {
  /* a run without a verdict still gets a viewer */
}

const html = `<title>${escape(path.basename(runDir))} board review</title>
<style>
  /* Palette taken from the board itself: FR4 substrate, solder mask, exposed copper,
     silkscreen. The accent is copper because that is the one colour on a PCB that
     signals "this carries current" — it earns being the only saturated thing here. */
  :root {
    color-scheme: light dark;
    --ground:    #f7f6f2;   /* bare FR4, sanded */
    --panel:     #fffefb;   /* silkscreen white */
    --ink:       #191b18;
    --muted:     #6d7169;   /* neutrals biased green: solder mask, not grey */
    --line:      #e0e0d6;
    --accent:    #a35a1e;   /* copper, darkened for contrast on a light ground */
    --accent-dim:#f0e2d3;
    --code-bg:   #f2f1eb;
    --pass:      #2f6b45;
    --warn:      #8a6412;
    --fail:      #a32c25;
    --pass-bg:   #e4efe7;
    --warn-bg:   #f6eed8;
    --fail-bg:   #f7e3e1;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground:    #14150f;   /* solder mask under low light */
      --panel:     #1c1e17;
      --ink:       #e9e9e0;
      --muted:     #969a8c;
      --line:      #2f3228;
      --accent:    #d99a5c;   /* copper lifts on a dark ground */
      --accent-dim:#3a2b1d;
      --code-bg:   #121309;
      --pass:      #7fc79b;
      --warn:      #e0be6a;
      --fail:      #e8897f;
      --pass-bg:   #1e2f24;
      --warn-bg:   #2f2818;
      --fail-bg:   #33201e;
    }
  }
  :root[data-theme="dark"] {
    --ground: #14150f; --panel: #1c1e17; --ink: #e9e9e0; --muted: #969a8c;
    --line: #2f3228; --accent: #d99a5c; --accent-dim: #3a2b1d; --code-bg: #121309;
    --pass: #7fc79b; --warn: #e0be6a; --fail: #e8897f;
    --pass-bg: #1e2f24; --warn-bg: #2f2818; --fail-bg: #33201e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--ground); color: var(--ink);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    font-variant-numeric: tabular-nums;
  }
  .wrap { max-width: 1360px; margin: 0 auto; padding: 0 24px; }
  header { padding-top: 26px; }
  .eyebrow {
    font: 600 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); margin: 0 0 9px;
  }
  h1 { font-size: 25px; margin: 0 0 4px; letter-spacing: -0.02em; text-wrap: balance; }
  .sub { color: var(--muted); font-size: 13.5px; margin: 0 0 18px; }
  .verdict {
    display: inline-flex; align-items: baseline; gap: 9px; padding: 7px 14px;
    border-radius: 999px; font-weight: 600; font-size: 13px; margin-bottom: 6px;
  }
  .verdict.fail { background: var(--fail-bg); color: var(--fail); }
  .verdict.pass { background: var(--pass-bg); color: var(--pass); }
  .verdict span { font-weight: 400; color: var(--ink); opacity: 0.75; }
  .gates { display: flex; flex-wrap: wrap; gap: 9px; margin: 14px 0 20px; }
  .gate {
    display: flex; flex-direction: column; gap: 2px; padding: 9px 13px;
    border-radius: 8px; border: 1px solid var(--line); background: var(--panel);
    border-left-width: 3px; min-width: 168px;
  }
  .gate b { font-size: 12px; font-weight: 650; letter-spacing: 0.01em; }
  .gate small { font-size: 11.5px; color: var(--muted);
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .gate.pass { border-left-color: var(--pass); }
  .gate.fail { border-left-color: var(--fail); }
  .gate.warn { border-left-color: var(--warn); }
  .gate.skip { border-left-color: var(--line); }
  .gate.fail b { color: var(--fail); }
  .gate.warn b { color: var(--warn); }
  .gate.skip b { color: var(--muted); }
  nav { display: flex; flex-wrap: wrap; gap: 2px; border-bottom: 1px solid var(--line); }
  nav button {
    appearance: none; border: 0; background: none; color: var(--muted);
    padding: 9px 15px; font: inherit; font-size: 14px; cursor: pointer;
    border-bottom: 2px solid transparent; margin-bottom: -1px; border-radius: 5px 5px 0 0;
  }
  nav button:hover { color: var(--ink); background: var(--accent-dim); }
  nav button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  nav button[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
  main { padding-top: 20px; padding-bottom: 64px; }
  section[hidden] { display: none; }
  .frame {
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 15px; overflow: auto;
  }
  .frame svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
  #three-root { height: min(72vh, 720px); position: relative; padding: 0; }
  #three-root canvas { display: block; border-radius: 9px; }
  #three-status { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); }
  pre {
    background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px;
    padding: 15px; overflow-x: auto; font-size: 12.5px; line-height: 1.55;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  h2 {
    font: 650 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
    letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted);
    margin: 26px 0 9px;
  }
  h2:first-child { margin-top: 0; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
  .hint { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
<div class="wrap">
<header>
  <p class="eyebrow">pcb-ai · ${escape(iterName)}</p>
  <h1>${escape(path.basename(runDir))}</h1>
  <p class="sub">${escape(String(latest?.components ?? "?"))} components · ${escape(String(latest?.nets ?? "?"))} nets${latest?.work_order != null ? ` · ${latest.work_order} item work order` : ""}</p>
  ${
    verdictPass === null
      ? ""
      : `<div class="verdict ${verdictPass ? "pass" : "fail"}">${verdictPass ? "ACCEPTED" : "REVISE"}${verdictSummary ? ` <span>${escape(verdictSummary)}</span>` : ""}</div>`
  }
  <div class="gates">
    ${gates
      .map(
        (g) =>
          `<div class="gate ${g.state}"><b>${escape(g.label)}</b><small>${escape(g.detail)}</small></div>`,
      )
      .join("\n    ")}
  </div>
  <nav id="tabs">
    ${tabs.map((t, i) => `<button role="tab" data-tab="${t.id}" aria-selected="${i === 0}">${escape(t.label)}</button>`).join("\n    ")}
  </nav>
</header>
<main>
  ${views
    .filter((v) => v.svg)
    .map(
      (v) =>
        `<section id="panel-${v.id}"${tabs[0].id === v.id ? "" : " hidden"}><div class="frame">${v.svg}</div></section>`,
    )
    .join("\n  ")}
  ${
    glbBase64
      ? `<section id="panel-three" hidden>
    <p class="hint">Drag to orbit · scroll to zoom · right-drag to pan</p>
    <div class="frame" id="three-root"><div id="three-status">Loading 3D model…</div></div>
  </section>`
      : ""
  }
  ${
    heatmaps.length
      ? `<section id="panel-heatmaps" hidden><div class="grid">
    ${heatmaps.map((h) => `<div><h2>${escape(h.label)}</h2><div class="frame">${h.svg}</div></div>`).join("\n    ")}
  </div></section>`
      : ""
  }
  ${
    reports.length
      ? `<section id="panel-reports" hidden>
    ${reports.map((r) => `<h2>${escape(r.label)}</h2><pre>${escape(r.text)}</pre>`).join("\n    ")}
    ${dfmText.trim() ? `<h2>Manufacturability (L8)</h2><pre>${escape(dfmText)}</pre>` : ""}
    ${verdict ? `<h2>Verdict</h2><pre>${escape(verdict)}</pre>` : ""}
    ${summary ? `<h2>Run summary</h2><pre>${escape(summary)}</pre>` : ""}
  </section>`
      : ""
  }
</main>
</div>
<script>
(function () {
  var nav = document.getElementById("tabs");
  nav.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    Array.prototype.forEach.call(nav.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-selected", String(b === btn));
    });
    Array.prototype.forEach.call(document.querySelectorAll("main > section"), function (s) {
      s.hidden = s.id !== "panel-" + btn.dataset.tab;
    });
    // The WebGL canvas was sized while its tab was hidden; re-measure on reveal.
    if (btn.dataset.tab === "three" && window.__initViewer__) window.__initViewer__();
  });
})();
</script>
${glbBase64 ? `<script>window.__GLB__=${JSON.stringify(glbBase64)};</script>` : ""}
<script>${bundle}</script>
`

const outFile = values.out ?? path.join(runDir, "viewer.html")
await fs.writeFile(outFile, html, "utf8")
const bytes = Buffer.byteLength(html)
console.log(`viewer → ${outFile}  (${(bytes / 1024 / 1024).toFixed(2)} MB)`)
console.log(`tabs: ${tabs.map((t) => t.label).join(", ")}`)
