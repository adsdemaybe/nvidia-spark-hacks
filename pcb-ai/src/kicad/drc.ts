/**
 * L5 second opinion — KiCad's DRC engine, run against our own board.
 *
 * Principle 4 asks for two independent implementations of every load-bearing check, and
 * says the interesting case is disagreement: "Agreement is signal; disagreement is a
 * pipeline bug worth more than either result."
 *
 * The first run proved the point immediately. On a rover that tscircuit's own DRC passed
 * with **zero errors**, KiCad 9 reported **46**: 23 `clearance` and 23 `hole_clearance`.
 * It also independently confirmed two things L8 had already measured — 54 silkscreen
 * texts below the legible height, and a hole-to-hole gap under the minimum — which is
 * the agreement half of the same principle, and is what makes the 46 credible rather
 * than suspicious.
 *
 * This runs `kicad-cli pcb drc` on the KiCad project `src/exports.ts` already writes, so
 * the second opinion costs one subprocess and no new representation of the board.
 */
import { execFile } from "node:child_process"
import fs from "node:fs/promises"
import path from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)

export interface KicadViolation {
  type: string
  severity: string
  description: string
}

export interface KicadDrcReport {
  available: boolean
  engine?: string
  errors: number
  warnings: number
  violations: KicadViolation[]
  unconnected: number
  problems: string[]
}

export interface KicadLocation {
  bin: string
  env: NodeJS.ProcessEnv
  version: string
}

let cached: KicadLocation | null | undefined

/**
 * Find kicad-cli: a system install first, then the vendored tree.
 *
 * The vendored copy needs three environment variables, not just a PATH entry — the
 * binary resolves its schemas and libraries from an absolute `/usr/share/kicad` prefix
 * that does not exist when the tree is relocated.
 */
export async function locateKicad(): Promise<KicadLocation | null> {
  if (cached !== undefined) return cached

  const vendored = path.resolve(process.cwd(), ".tools/kicad")
  const candidates = [
    { bin: "kicad-cli", env: process.env },
    {
      bin: path.join(vendored, "usr/bin/kicad-cli"),
      env: {
        ...process.env,
        LD_LIBRARY_PATH: [
          path.join(vendored, "usr/lib/aarch64-linux-gnu"),
          process.env.LD_LIBRARY_PATH,
        ]
          .filter(Boolean)
          .join(":"),
        KICAD_DATA: path.join(vendored, "usr/share/kicad"),
        KICAD9_DATA: path.join(vendored, "usr/share/kicad"),
      },
    },
  ]

  for (const c of candidates) {
    try {
      const { stdout, stderr } = await run(c.bin, ["--version"], {
        env: c.env,
        timeout: 30_000,
      })
      const version = `${stdout}${stderr}`.match(/(\d+\.\d+\.\d+)/)?.[1] ?? "unknown"
      cached = { bin: c.bin, env: c.env, version }
      return cached
    } catch {
      /* try the next */
    }
  }
  cached = null
  return cached
}

/**
 * Run KiCad's DRC on a `.kicad_pcb`.
 *
 * A missing kicad-cli is reported, never fatal: the second opinion is evidence, and its
 * absence weakens the verdict without invalidating the board.
 */
export async function runKicadDrc(args: {
  kicadPcb: string
  dir: string
}): Promise<KicadDrcReport> {
  const loc = await locateKicad()
  if (!loc) {
    return {
      available: false,
      errors: 0,
      warnings: 0,
      violations: [],
      unconnected: 0,
      problems: [
        "kicad-cli not found — the second-opinion DRC did not run. Install it with " +
          "`./tools/vendor-kicad.sh` (no root required).",
      ],
    }
  }

  await fs.mkdir(args.dir, { recursive: true })
  const out = path.join(args.dir, "kicad-drc.json")

  try {
    await run(loc.bin, ["pcb", "drc", "--format", "json", "-o", out, args.kicadPcb], {
      env: loc.env,
      timeout: 300_000,
      maxBuffer: 64 * 1024 * 1024,
    })
  } catch (err: any) {
    // `--exit-code-violations` is not passed, so a non-zero exit means the run itself
    // failed rather than the board being dirty.
    const detail = String(err?.stderr || err?.stdout || err?.message || err).split("\n")[0]
    if (!(await fileExists(out))) {
      return {
        available: true,
        engine: `kicad-cli ${loc.version}`,
        errors: 0,
        warnings: 0,
        violations: [],
        unconnected: 0,
        problems: [`kicad-cli could not check this board: ${detail}`],
      }
    }
  }

  let parsed: any
  try {
    parsed = JSON.parse(await fs.readFile(out, "utf8"))
  } catch (err) {
    return {
      available: true,
      engine: `kicad-cli ${loc.version}`,
      errors: 0,
      warnings: 0,
      violations: [],
      unconnected: 0,
      problems: [`could not parse the DRC report: ${(err as Error).message}`],
    }
  }

  const violations: KicadViolation[] = (parsed.violations ?? []).map((v: any) => ({
    type: String(v.type ?? "unknown"),
    severity: String(v.severity ?? "warning"),
    description: String(v.description ?? ""),
  }))

  return {
    available: true,
    engine: `kicad-cli ${loc.version}`,
    errors: violations.filter((v) => v.severity === "error").length,
    warnings: violations.filter((v) => v.severity === "warning").length,
    violations,
    unconnected: (parsed.unconnected_items ?? []).length,
    problems: [],
  }
}

export function describeKicadDrc(report: KicadDrcReport): string {
  const lines: string[] = []
  if (!report.available) {
    lines.push("KICAD DRC  NOT RUN — no second opinion on this board.")
    lines.push(...report.problems.map((p) => `  ${p}`))
    return lines.join("\n")
  }

  lines.push(
    `KICAD DRC  ${report.engine} — ${report.errors} error(s), ${report.warnings} warning(s), ` +
      `${report.unconnected} unconnected item(s)`,
  )
  if (report.problems.length) {
    lines.push(...report.problems.map((p) => `  ${p}`))
    return lines.join("\n")
  }

  const byType = new Map<string, { n: number; severity: string; example: string }>()
  for (const v of report.violations) {
    const e = byType.get(v.type) ?? { n: 0, severity: v.severity, example: v.description }
    e.n++
    byType.set(v.type, e)
  }
  lines.push("")
  for (const [type, e] of [...byType.entries()].sort((a, b) => b[1].n - a[1].n)) {
    lines.push(`  ${String(e.n).padStart(4)}  [${e.severity}] ${type}`)
  }
  return lines.join("\n")
}

/** Errors only — what may block promotion. */
export function kicadDrcBlockers(report: KicadDrcReport): string[] {
  const byType = new Map<string, number>()
  for (const v of report.violations) {
    if (v.severity !== "error") continue
    byType.set(v.type, (byType.get(v.type) ?? 0) + 1)
  }
  return [...byType.entries()].map(
    ([type, n]) => `KiCad DRC: ${n}x ${type} (second-opinion engine; tscircuit's DRC did not report these)`,
  )
}

async function fileExists(f: string) {
  try {
    await fs.access(f)
    return true
  } catch {
    return false
  }
}
