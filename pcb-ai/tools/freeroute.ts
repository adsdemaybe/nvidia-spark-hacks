/**
 * Route a board with Freerouting instead of tscircuit's capacity-autorouter, and compare.
 *
 *   npx tsx tools/freeroute.ts runs/rover-motor-driver/iter-0/circuit.json
 *
 * Freerouting is the long-standing open-source Specctra autorouter — the one KiCad has
 * shipped an export path for since forever. It is worth measuring against because routing
 * is where this pipeline actually fails: the compile errors that block a board are
 * dominated by `pcb_autorouting_error` and its downstream "not connected" consequences,
 * and a second router is the only way to tell "this board is unroutable" apart from "our
 * router could not do it".
 *
 * The exchange format is Specctra: DSN in (board, parts, nets, rules), SES out (the
 * routed session). `dsn-converter` handles both directions against Circuit JSON, so
 * nothing here parses S-expressions by hand.
 *
 * Java 25, not 21. Freerouting 2.3.0's classes are file version 69, and a Java 21 runtime
 * refuses them with `UnsupportedClassVersionError ... recognizes class file versions up to
 * 65.0` — which reads like a corrupt jar. The shipped Linux installer is x86-64 only, so
 * on this box it is the platform-independent jar plus an aarch64 JRE.
 */

import { execFileSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const VENDOR = path.join(HERE, "..", "vendor")

function javaBin(): string {
  const jre = fs.readdirSync(VENDOR).find((d) => d.startsWith("jdk-25"))
  if (!jre) {
    throw new Error(
      `no Java 25 runtime in ${VENDOR}. Freerouting 2.3 needs one (class file version 69); ` +
        `fetch it with tools/vendor-freerouting.sh`,
    )
  }
  return path.join(VENDOR, jre, "bin", "java")
}

/** Traces and vias actually present in a Circuit JSON, for comparing two routers. */
export function routingStats(circuitJson: any[]) {
  const traces = circuitJson.filter((e) => e.type === "pcb_trace")
  const vias = circuitJson.filter((e) => e.type === "pcb_via")
  let length = 0
  for (const t of traces) {
    const route = t.route ?? []
    for (let i = 1; i < route.length; i++) {
      const a = route[i - 1]
      const b = route[i]
      if (a?.x == null || b?.x == null) continue
      length += Math.hypot(b.x - a.x, b.y - a.y)
    }
  }
  return { traces: traces.length, vias: vias.length, length_mm: Number(length.toFixed(2)) }
}

/**
 * Remove the `(wiring ...)` scope, leaving board, parts, nets and rules.
 *
 * Two reasons, and either alone would justify it.
 *
 * **It is the experiment.** Handing a router a board that is already routed asks it to
 * re-route around existing copper, which is a different and much easier question than the
 * one being asked. Freerouting should get the same unrouted problem tscircuit got.
 *
 * **The wiring dsn-converter emits is malformed.** Specctra wants
 * `(via <padstack> <x> <y> (net <n>))`; it writes `(via (path F.Cu 600 x y)(net "BIN2"))`
 * with no padstack name, and freerouting stops at `Wiring.read_via_scope: padstack name
 * expected at 'BIN2'` and then NPEs because the board never loaded. The padstack itself is
 * declared correctly further up the file, so this is the wiring writer, not the library.
 */
function stripWiring(dsn: string): string {
  const start = dsn.indexOf("(wiring")
  if (start === -1) return dsn
  let depth = 0
  for (let i = start; i < dsn.length; i++) {
    if (dsn[i] === "(") depth++
    else if (dsn[i] === ")") {
      depth--
      if (depth === 0) return dsn.slice(0, start) + dsn.slice(i + 1)
    }
  }
  return dsn.slice(0, start) + ")"
}

async function main(): Promise<number> {
  const input = process.argv[2]
  if (!input) {
    console.error("usage: tsx tools/freeroute.ts <circuit.json> [outdir]")
    return 2
  }
  const outdir = process.argv[3] ?? path.join(path.dirname(input), "freerouting")
  fs.mkdirSync(outdir, { recursive: true })

  const raw = JSON.parse(fs.readFileSync(input, "utf8"))
  const circuitJson: any[] = Array.isArray(raw) ? raw : (raw.circuitJson ?? raw.circuit_json ?? [])
  const before = routingStats(circuitJson)
  console.log(`input   ${circuitJson.length} elements`)
  console.log(`  tscircuit routed: ${before.traces} traces, ${before.vias} vias, ${before.length_mm}mm`)

  const { convertCircuitJsonToDsnString, convertDsnSessionToCircuitJson, parseDsnToDsnJson } =
    await import("dsn-converter")

  const dsnPath = path.join(outdir, "board.dsn")
  const sesPath = path.join(outdir, "board.ses")
  fs.writeFileSync(dsnPath, stripWiring(convertCircuitJsonToDsnString(circuitJson as any)))
  console.log(`  wrote ${dsnPath} (${(fs.statSync(dsnPath).size / 1024).toFixed(1)} kB)`)

  const t0 = Date.now()
  try {
    execFileSync(
      javaBin(),
      [
        "-jar", path.join(VENDOR, "freerouting.jar"),
        "-de", dsnPath,
        "-do", sesPath,
      ],
      { stdio: ["ignore", "pipe", "pipe"], timeout: 15 * 60_000, encoding: "utf8" },
    )
  } catch (err: any) {
    const out = `${err.stdout ?? ""}${err.stderr ?? ""}`.trim()
    // Freerouting exits non-zero in cases where it still wrote a session, so the file is
    // the source of truth rather than the exit code.
    if (!fs.existsSync(sesPath)) {
      console.error(`freerouting failed and wrote no session:\n${out.slice(-1500)}`)
      return 1
    }
    console.log(`  (freerouting exited non-zero but produced a session)`)
  }
  const secs = ((Date.now() - t0) / 1000).toFixed(1)

  if (!fs.existsSync(sesPath)) {
    console.error("no session file produced")
    return 1
  }
  console.log(`  freerouting finished in ${secs}s -> ${sesPath}`)

  // Both arguments are parsed objects. Passing the raw .ses text lands as
  // `Cannot read properties of undefined (reading 'network_out')` several frames down,
  // which reads like freerouting produced a bad session — it did not.
  const routed = convertDsnSessionToCircuitJson(
    parseDsnToDsnJson(fs.readFileSync(dsnPath, "utf8")) as any,
    parseDsnToDsnJson(fs.readFileSync(sesPath, "utf8")) as any,
  )
  // Completeness first, and it is not optional. A router that leaves nets unrouted always
  // looks better on via count and total length, because the connections it skipped cost
  // nothing. On `regress` freerouting reported 0 vias against tscircuit's 83 and a 20%
  // shorter route — and had routed 30 of 38 nets. Comparing cost before confirming
  // coverage would have made that look like a decisive win.
  const dsnJson: any = parseDsnToDsnJson(fs.readFileSync(dsnPath, "utf8"))
  const sesJson: any = parseDsnToDsnJson(fs.readFileSync(sesPath, "utf8"))
  const needed = (dsnJson.network?.nets ?? []).filter((n: any) => (n.pins ?? []).length > 1)
  const routedNames = new Set((sesJson.routes?.network_out?.nets ?? []).map((n: any) => n.name))
  const missing = needed.filter((n: any) => !routedNames.has(n.name)).map((n: any) => n.name)

  const after = routingStats(routed as any[])
  fs.writeFileSync(path.join(outdir, "routed.circuit.json"), JSON.stringify(routed, null, 2))

  console.log()
  console.log(`  nets needing routes: ${needed.length}, freerouting completed ${needed.length - missing.length}`)
  if (missing.length) {
    console.log(`  ** INCOMPLETE — ${missing.length} net(s) unrouted **`)
    for (const n of missing.slice(0, 6)) console.log(`       ${n}`)
    if (missing.length > 6) console.log(`       … and ${missing.length - 6} more`)
  }

  console.log()
  console.log(`  ${"router".padEnd(14)} ${"segs".padStart(6)} ${"vias".padStart(6)} ${"length".padStart(10)}`)
  console.log(`  ${"tscircuit".padEnd(14)} ${String(before.traces).padStart(6)} ${String(before.vias).padStart(6)} ${(before.length_mm + "mm").padStart(10)}`)
  console.log(`  ${"freerouting".padEnd(14)} ${String(after.traces).padStart(6)} ${String(after.vias).padStart(6)} ${(after.length_mm + "mm").padStart(10)}`)
  // Segment counts are not comparable between the two: freerouting emits one wire per
  // straight run while tscircuit emits one pcb_trace per connection. Vias and total length
  // are the figures that mean the same thing on both sides.
  if (missing.length) {
    console.log()
    console.log(`  Those numbers are not comparable: an incomplete route is cheaper by`)
    console.log(`  construction. Treat this as a failure to route, not a better route.`)
    return 1
  }
  return 0
}

main().then((c) => process.exit(c))
