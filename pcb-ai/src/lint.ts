/**
 * L0 of the deterministic ladder: lint the HDL before spending a compile on it.
 *
 * Engine is ESLint + typescript-eslint; the electronics rules live in
 * lint/pcb-plugin.mjs. A lint error costs ~50ms to surface; the same defect found by
 * the compiler costs a 40–60s compile-route cycle, and found by the router costs an
 * entire review iteration.
 */
import { ESLint } from "eslint"
import path from "node:path"
import { fileURLToPath } from "node:url"

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

export interface LintFinding {
  ruleId: string
  severity: "error" | "warning"
  line: number
  message: string
}

let eslint: ESLint | undefined

/** Lint one HDL module (given as source text). Returns findings, errors first. */
export async function lintHdl(code: string): Promise<LintFinding[]> {
  eslint ??= new ESLint({
    cwd: projectRoot,
    overrideConfigFile: path.join(projectRoot, "eslint.config.mjs"),
  })
  // A virtual path inside boards/ so the HDL file-pattern block of the config applies.
  const [result] = await eslint.lintText(code, {
    filePath: path.join(projectRoot, "boards", "__candidate__.tsx"),
  })
  return (result?.messages ?? [])
    .map((m) => ({
      ruleId: m.ruleId ?? "parse",
      severity: m.severity === 2 ? ("error" as const) : ("warning" as const),
      line: m.line ?? 0,
      message: m.message,
    }))
    .sort((a, b) => (a.severity === b.severity ? a.line - b.line : a.severity === "error" ? -1 : 1))
}

/** Render findings for the designer agent — written to be actionable by a model. */
export function describeLint(findings: LintFinding[]): string {
  if (!findings.length) return "Lint: clean."
  const errors = findings.filter((f) => f.severity === "error")
  const lines = [`Lint: ${errors.length} error(s), ${findings.length - errors.length} warning(s).`]
  for (const f of findings) {
    lines.push(`  ${f.severity.toUpperCase()} L${f.line} [${f.ruleId}] ${f.message}`)
  }
  return lines.join("\n")
}
