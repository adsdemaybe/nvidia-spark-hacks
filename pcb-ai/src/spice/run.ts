/**
 * Running ngspice, and reading numbers back out of it.
 *
 * ngspice is a subprocess, not a library binding: batch mode (`-b`) with a `.control`
 * block is scriptable, has no GUI, and fails with a non-zero exit and a readable error.
 * The parsing target is deliberately narrow — `print` output from a `.control` block,
 * which ngspice writes as `name = value` — because parsing ngspice's free-form log is a
 * source of silent wrong answers.
 *
 * A simulation that does not converge is not a measurement. Every failure path here
 * returns an error rather than a number, and the stage above turns that into a gate
 * failure instead of a pass with missing data.
 */
import { execFile } from "node:child_process"
import fs from "node:fs/promises"
import path from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)

export interface NgspiceLocation {
  bin: string
  env: NodeJS.ProcessEnv
  version: string
  source: string
}

let cached: NgspiceLocation | null | undefined

/**
 * Find ngspice.
 *
 * `.tools/ngspice` is the vendored copy — the Ubuntu package extracted in place, which
 * is how this runs on a Spark where `sudo apt install` is not available to the agent.
 * A system install on PATH wins if there is one, because that is what a developer
 * expects to be using.
 */
export async function locateNgspice(): Promise<NgspiceLocation | null> {
  if (cached !== undefined) return cached

  const candidates: Array<{ bin: string; libs?: string; share?: string; source: string }> = [
    { bin: "ngspice", source: "PATH" },
  ]
  const vendored = path.resolve(process.cwd(), ".tools/ngspice")
  candidates.push({
    bin: path.join(vendored, "usr/bin/ngspice"),
    libs: path.join(vendored, "usr/lib/aarch64-linux-gnu"),
    share: path.join(vendored, "usr/share/ngspice"),
    source: ".tools/ngspice",
  })

  for (const c of candidates) {
    const env = {
      ...process.env,
      ...(c.libs
        ? { LD_LIBRARY_PATH: [c.libs, process.env.LD_LIBRARY_PATH].filter(Boolean).join(":") }
        : {}),
      ...(c.share ? { SPICE_LIB_DIR: c.share } : {}),
    }
    try {
      const { stdout, stderr } = await run(c.bin, ["--version"], { env, timeout: 15_000 })
      const text = `${stdout}${stderr}`
      const version = text.match(/ngspice-(\S+)/)?.[1] ?? "unknown"
      cached = { bin: c.bin, env, version, source: c.source }
      return cached
    } catch {
      // Try the next candidate.
    }
  }
  cached = null
  return cached
}

export interface SimResult {
  ok: boolean
  /** `print` results, keyed by the expression as ngspice echoed it. */
  values: Map<string, number>
  stdout: string
  error?: string
}

/**
 * Run one deck and read back every `print`ed value.
 *
 * `deck` must already contain its analysis and `.control` block; this function only
 * adds the file, the run and the parse.
 */
export async function simulate(args: {
  deck: string
  dir: string
  name: string
}): Promise<SimResult> {
  const loc = await locateNgspice()
  if (!loc) {
    return {
      ok: false,
      values: new Map(),
      stdout: "",
      error:
        "ngspice not found. Install it with `sudo apt install ngspice`, or vendor it " +
        "without root: `apt-get download ngspice libngspice0 && dpkg -x <deb> .tools/ngspice`.",
    }
  }

  await fs.mkdir(args.dir, { recursive: true })
  const file = path.join(args.dir, `${args.name}.cir`)
  await fs.writeFile(file, args.deck, "utf8")

  let stdout = ""
  let stderr = ""
  try {
    const result = await run(loc.bin, ["-b", file], {
      env: loc.env,
      timeout: 120_000,
      maxBuffer: 32 * 1024 * 1024,
    })
    stdout = result.stdout
    stderr = result.stderr
  } catch (err: any) {
    stdout = err?.stdout ?? ""
    stderr = err?.stderr ?? String(err?.message ?? err)
    await fs.writeFile(path.join(args.dir, `${args.name}.log`), `${stdout}\n${stderr}`, "utf8")
    return { ok: false, values: new Map(), stdout, error: firstError(stderr, stdout) }
  }

  await fs.writeFile(path.join(args.dir, `${args.name}.log`), `${stdout}\n${stderr}`, "utf8")

  // ngspice reports singular matrices and non-convergence on stdout with a zero exit.
  // Treating that as success would hand the gate a number that means nothing.
  const fatal = firstError(stderr, stdout)
  if (fatal) return { ok: false, values: new Map(), stdout, error: fatal }

  return { ok: true, values: parsePrints(stdout), stdout }
}

const FATAL_PATTERNS = [
  /singular matrix/i,
  /no convergence/i,
  /doAnalyses:\s*\S+/i,
  /fatal error/i,
  /Error on line/i,
  /unknown subckt/i,
  /is not a (?:node|device)/i,
]

function firstError(stderr: string, stdout: string): string | undefined {
  const text = `${stderr}\n${stdout}`
  for (const pattern of FATAL_PATTERNS) {
    const m = text.match(new RegExp(`^.*${pattern.source}.*$`, "im"))
    if (m) return m[0].trim()
  }
  return undefined
}

/**
 * Read `print` output.
 *
 * ngspice writes scalar prints as `v(vout) = 3.700000e+00` and vector prints as an
 * indexed table. Only the scalar form is parsed; a claim needing a waveform states its
 * own measurement with `.measure` and prints the scalar result.
 */
function parsePrints(stdout: string): Map<string, number> {
  const values = new Map<string, number>()
  // Names include element-parameter references like `@rr2[i]`, which start with `@` and
  // carry brackets — the form every branch-current print uses.
  const line = /^\s*([a-zA-Z_@][\w().@#$[\]+-]*)\s*=\s*(-?[\d.]+(?:[eE][+-]?\d+)?)\s*$/gm
  let m: RegExpExecArray | null
  while ((m = line.exec(stdout))) {
    values.set(m[1].toLowerCase(), Number(m[2]))
  }
  return values
}
