/**
 * The design loop as a LangGraph state machine.
 *
 *                     ┌──────────────── revise ◀──────────────┐
 *                     ▼                                       │
 *   START ─▶ design ─▶ compile ─┬─▶ compile_failed ───────────▶│
 *                               │                              │
 *                               └─▶ operating_point ─▶ physics ─┬─▶ review_layout ─┐
 *                                                               ├─▶ review_spec   ─┼─▶ chief ─┤
 *                                                               └─▶ review_physics ┘          │
 *                                                                                  finalize ◀─┘
 *                                                                                     │
 *                                                                                    END
 *
 * The three reviews are separate nodes with a common predecessor and a common
 * successor, so LangGraph runs them in one superstep and `chief` does not start until
 * all three have landed. `revise → compile` is the recurrent edge; the conditional
 * edge out of `chief` is the only thing that ends the loop.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { StateGraph, Annotation, START, END, MemorySaver } from "@langchain/langgraph"

import { build, describeBuild, isLowSignal } from "./build.ts"
import type { BuildResult } from "./types.ts"
import { lintHdl, describeLint } from "./lint.ts"
import { runPhysics, describePhysics, physicsBlockers } from "./physics/index.ts"
import { runSpice, describeSpice, type Claim, type SpiceReport } from "./spice/index.ts"
import { runDfm, describeDfm, dfmBlockers, type DfmReport } from "./dfm/index.ts"
import { DEFAULT_PROFILE, describeProfile, type FabProfile } from "./dfm/profile.ts"
import type { PhysicsReport } from "./physics/index.ts"
import { exportFabrication } from "./fab.ts"
import { designFromSpec, reviseDesign } from "./agents/designer.ts"
import { selectParts, describePartsPlan } from "./agents/parts.ts"
import { modelOperatingPoint } from "./agents/modeler.ts"
import { reviewLayout } from "./agents/layout.ts"
import { reviewSpec } from "./agents/spec.ts"
import { reviewPhysics } from "./agents/physicist.ts"
import { decide } from "./agents/chief.ts"
import type { ModelRoster } from "./model.ts"
import type { OperatingPoint, PartsPlan, Review, Verdict } from "./schemas.ts"

const SEVERITY_ORDER = { blocker: 0, major: 1, minor: 2 } as const

/** Written to summary.json — one row per trip around the loop. */
export interface IterationRecord {
  index: number
  compiled: boolean
  components: number
  nets: number
  peak_temperature_c?: number
  max_ir_drop_mv?: number
  drc_errors?: number
  hard_failures?: number
  spice_claims_passing?: string
  spice_model_coverage_pct?: number
  dfm_errors?: number
  dfm_warnings?: number
  findings: Record<string, number>
  work_order: number
  pass: boolean
  summary: string
}

const State = Annotation.Root({
  spec: Annotation<string>,
  outDir: Annotation<string>,
  maxIterations: Annotation<number>,
  iteration: Annotation<number>({ reducer: (_, b) => b, default: () => 0 }),
  code: Annotation<string>({ reducer: (a, b) => b ?? a, default: () => "" }),
  /** Chosen once, up front, then held fixed for every later iteration. */
  plan: Annotation<PartsPlan | undefined>({ reducer: (a, b) => b ?? a }),
  build: Annotation<BuildResult | undefined>({ reducer: (a, b) => b ?? a }),
  operatingPoint: Annotation<OperatingPoint | undefined>({ reducer: (a, b) => b ?? a }),
  physics: Annotation<PhysicsReport | undefined>({ reducer: (_, b) => b }),
  spice: Annotation<SpiceReport | undefined>({ reducer: (_, b) => b }),
  dfm: Annotation<DfmReport | undefined>({ reducer: (_, b) => b }),
  /** Merged, not replaced: the three review nodes write concurrently. */
  reviews: Annotation<Record<string, Review>>({
    reducer: (a, b) => ({ ...a, ...b }),
    default: () => ({}),
  }),
  verdict: Annotation<Verdict | undefined>({ reducer: (a, b) => b ?? a }),
  history: Annotation<IterationRecord[]>({
    reducer: (a, b) => [...a, ...b],
    default: () => [],
  }),
})

type GraphState = typeof State.State

export interface GraphDeps {
  models: ModelRoster
  /** Analyse against this instead of asking the modelling agent for one. */
  fixedOperatingPoint?: OperatingPoint
  /** L7 claims to assert against this board (§3.4). No claims = no electrical verification. */
  claims?: Claim[]
  /** L8 fab limits. Defaults to a conservative generic profile, never to "anything goes". */
  fabProfile?: FabProfile
}

const iterDir = (s: GraphState) => path.join(s.outDir, `iter-${s.iteration}`)

export function buildGraph(deps: GraphDeps) {
  const { models, fixedOperatingPoint, claims = [], fabProfile = DEFAULT_PROFILE } = deps

  // ── nodes ────────────────────────────────────────────────────────────────────

  async function parts(state: GraphState) {
    const plan = await selectParts({ model: models.parts, spec: state.spec })
    console.log(
      `  parts     ${plan.topology} — ${plan.parts.length} parts, ` +
        `${plan.rails.length} rail(s)`,
    )
    for (const p of plan.parts) {
      console.log(`            ${p.ref.padEnd(4)} ${p.value.padEnd(10)} ${p.footprint}`)
    }
    await fs.writeFile(
      path.join(state.outDir, "parts-plan.json"),
      JSON.stringify(plan, null, 2),
    )
    await fs.writeFile(path.join(state.outDir, "parts-plan.txt"), describePartsPlan(plan))
    return { plan }
  }

  async function design(state: GraphState) {
    console.log("  design    parts plan → HDL")
    return { code: await designFromSpec(models.designer, state.spec, state.plan) }
  }

  async function compile(state: GraphState) {
    const dir = iterDir(state)
    console.log(`\n── iteration ${state.iteration} ──`)

    // L0: lint before spending a compile. Lint errors are compile failures that
    // happened to be caught 1000x earlier — route them down the same failure path.
    const lint = await lintHdl(state.code)
    const lintErrors = lint.filter((f) => f.severity === "error")
    await fs.mkdir(dir, { recursive: true })
    await fs.writeFile(path.join(dir, "lint.txt"), describeLint(lint))
    if (lintErrors.length) {
      console.log(`  lint      ${lintErrors.length} error(s) — compile skipped`)
      const result: BuildResult = {
        ok: false,
        compileError: `HDL failed lint (compile not attempted):\n${describeLint(lintErrors)}`,
        circuitJson: [],
        findings: [],
        netlist: [],
        components: [],
        images: {},
      }
      await fs.writeFile(path.join(dir, "circuit.tsx"), state.code)
      return { build: result, physics: undefined, reviews: {} }
    }
    console.log(`  lint      clean${lint.length ? ` (${lint.length} warning(s))` : ""}`)

    const result = await build(state.code, dir)
    await fs.writeFile(path.join(dir, "report.txt"), describeBuild(result))

    if (result.compileError) {
      console.log(`  compile   FAILED — ${result.compileError.split("\n")[0]}`)
    } else {
      const errors = result.findings.filter((f) => f.severity === "error").length
      const warnings = result.findings.filter(
        (f) => f.severity === "warning" && !isLowSignal(f),
      ).length
      console.log(
        `  compile   ${result.components.length} parts, ${result.netlist.length} nets, ` +
          `${errors} errors, ${warnings} warnings`,
      )
    }
    // A fresh compile invalidates the previous iteration's analysis.
    return { build: result, physics: undefined, reviews: {} }
  }

  /**
   * A design that does not compile has no geometry to analyse and nothing to look at,
   * so the loop short-circuits to a verdict the designer can act on.
   */
  async function compileFailed(state: GraphState) {
    const message = state.build?.compileError?.split("\n")[0] ?? "unknown compile error"
    const verdict: Verdict = {
      pass: false,
      summary: "The HDL did not compile, so nothing could be routed, analysed or reviewed.",
      work_order: [
        {
          severity: "blocker",
          category: "connectivity",
          description: `Compile failure: ${message}`,
          suggested_fix:
            "Fix the syntax or API error. Use only documented tscircuit elements and props, and do not import anything.",
          source: "compiler",
        },
      ],
    }
    await fs.writeFile(
      path.join(iterDir(state), "verdict.json"),
      JSON.stringify(verdict, null, 2),
    )
    return { verdict, history: [record(state, verdict, {})] }
  }

  async function operatingPoint(state: GraphState) {
    const op =
      fixedOperatingPoint ??
      (await modelOperatingPoint({
        model: models.modeler,
        spec: state.spec,
        code: state.code,
        build: state.build!,
      }))
    console.log(
      `  model     ${op.rails.length} rail(s), ${op.loads.length} load(s), ` +
        `${op.dissipation.reduce((s, d) => s + d.power_w, 0).toFixed(3)}W total`,
    )
    await fs.writeFile(
      path.join(iterDir(state), "operating-point.json"),
      JSON.stringify(op, null, 2),
    )
    return { operatingPoint: op }
  }

  async function physics(state: GraphState) {
    const dir = iterDir(state)
    const report = await runPhysics(
      state.build!.circuitJson,
      state.operatingPoint!,
      path.join(dir, "physics"),
    )
    await fs.writeFile(path.join(dir, "physics.txt"), describePhysics(report))
    const hard = physicsBlockers(report)
    console.log(
      `  physics   peak ${report.thermal?.peak_c.toFixed(1) ?? "?"}°C, ` +
        `${report.rails.length} rail(s) solved, ` +
        `${report.geometry.filter((v) => v.severity === "error").length} DRC errors, ` +
        `${hard.length} hard failure(s)`,
    )
    // L7 rides in this node rather than its own. Both stages consume exactly
    // `build` + `operatingPoint`, both are deterministic, and neither can start before
    // the other's inputs exist — a separate node would buy a superstep and no
    // concurrency. The reports stay separate; only the scheduling is shared.
    const spice = await runSpice({
      build: state.build!,
      operatingPoint: state.operatingPoint!,
      claims,
      dir,
    })
    await fs.writeFile(path.join(dir, "spice.txt"), describeSpice(spice))
    if (!spice.available) {
      console.log("  spice     SKIPPED — ngspice not installed (tools/vendor-ngspice.sh)")
    } else {
      const substantive = spice.claims.filter((c) => !c.tautological)
      console.log(
        `  spice     ${spice.claims.filter((c) => c.pass).length}/${spice.claims.length} claim(s) pass ` +
          `(${substantive.length} able to fail), ${spice.coveragePercent.toFixed(0)}% model coverage, ` +
          `${spice.hardFailures.length} hard failure(s)`,
      )
    }

    // L8 — fab limits. Same node again, same reason: it needs only the compiled
    // geometry, which has existed since L1.
    const dfm = runDfm(state.build!.circuitJson, fabProfile)
    await fs.writeFile(
      path.join(dir, "dfm.txt"),
      `${describeProfile(fabProfile)}\n\n${describeDfm(dfm)}`,
    )
    console.log(
      `  dfm       ${dfm.errors} error(s), ${dfm.warnings} warning(s) vs "${fabProfile.name}"`,
    )

    return { physics: report, spice, dfm }
  }

  /** The three reviews below run concurrently; each writes its own key. */

  async function layoutNode(state: GraphState) {
    const review = await reviewLayout({
      model: models.layout,
      spec: state.spec,
      build: state.build!,
    })
    console.log(`  layout    ${review.findings.length} finding(s) — ${review.summary}`)
    return { reviews: { layout: review } }
  }

  async function specNode(state: GraphState) {
    const review = await reviewSpec({
      model: models.spec,
      spec: state.spec,
      code: state.code,
      build: state.build!,
    })
    console.log(`  spec      ${review.findings.length} finding(s) — ${review.summary}`)
    return { reviews: { spec: review } }
  }

  async function physicsReviewNode(state: GraphState) {
    const review = await reviewPhysics({
      model: models.physicist,
      spec: state.spec,
      physics: state.physics!,
    })
    console.log(`  physicist ${review.findings.length} finding(s) — ${review.summary}`)
    return { reviews: { physics: review } }
  }

  async function chief(state: GraphState) {
    const dir = iterDir(state)
    await fs.writeFile(
      path.join(dir, "reviews.json"),
      JSON.stringify(state.reviews, null, 2),
    )

    const verdict = await decide({
      model: models.chief,
      spec: state.spec,
      build: state.build!,
      physics: state.physics,
      spice: state.spice,
      dfm: state.dfm,
      reviews: state.reviews,
    })
    verdict.work_order.sort(
      (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
    )
    await fs.writeFile(path.join(dir, "verdict.json"), JSON.stringify(verdict, null, 2))

    const counts = verdict.work_order.reduce<Record<string, number>>((acc, i) => {
      acc[i.severity] = (acc[i.severity] ?? 0) + 1
      return acc
    }, {})
    console.log(
      `  verdict   ${verdict.pass ? "PASS" : "REVISE"} — ` +
        `${counts.blocker ?? 0} blocker, ${counts.major ?? 0} major, ${counts.minor ?? 0} minor`,
    )
    console.log(`            ${verdict.summary}`)
    for (const item of verdict.work_order.slice(0, 6)) {
      console.log(`            · [${item.severity}] ${item.description}`)
    }

    return { verdict, history: [record(state, verdict, state.reviews)] }
  }

  async function revise(state: GraphState) {
    console.log("  revising…")
    const code = await reviseDesign({
      model: models.designer,
      spec: state.spec,
      plan: state.plan,
      code: state.code,
      build: state.build!,
      physics: state.physics,
      verdict: state.verdict!,
    })
    return { code, iteration: state.iteration + 1 }
  }

  async function finalize(state: GraphState) {
    await fs.writeFile(path.join(state.outDir, "final.tsx"), state.code)

    if (state.build && !state.build.compileError) {
      const fabDir = path.join(state.outDir, "fabrication")
      const files = await exportFabrication(state.build.circuitJson, fabDir)
      console.log(
        `\nfabrication  ${files.length} files → ${path.relative(process.cwd(), fabDir)}`,
      )
    }

    await fs.writeFile(
      path.join(state.outDir, "summary.json"),
      JSON.stringify(
        { iterations: state.history, accepted: state.verdict?.pass ?? false },
        null,
        2,
      ),
    )

    console.log(
      `\n${state.verdict?.pass ? "accepted" : "budget exhausted, not accepted"} ` +
        `after ${state.history.length} iteration(s) → ${path.relative(process.cwd(), state.outDir)}`,
    )
    return {}
  }

  // ── edges ────────────────────────────────────────────────────────────────────

  const graph = new StateGraph(State)
    .addNode("parts", parts)
    .addNode("design", design)
    .addNode("compile", compile)
    .addNode("compile_failed", compileFailed)
    .addNode("operating_point", operatingPoint)
    .addNode("solve", physics)
    .addNode("review_layout", layoutNode)
    .addNode("review_spec", specNode)
    .addNode("review_physics", physicsReviewNode)
    .addNode("chief", chief)
    .addNode("revise", revise)
    .addNode("finalize", finalize)

    // A seed design skips both the parts decision and generation.
    .addConditionalEdges(START, (s: GraphState) => (s.code ? "compile" : "parts"), {
      parts: "parts",
      compile: "compile",
    })
    .addEdge("parts", "design")
    .addEdge("design", "compile")
    .addConditionalEdges(
      "compile",
      (s: GraphState) => (s.build?.compileError ? "compile_failed" : "operating_point"),
      { compile_failed: "compile_failed", operating_point: "operating_point" },
    )
    .addEdge("operating_point", "solve")

    // Fan out: one superstep, three independent reviews.
    .addEdge("solve", "review_layout")
    .addEdge("solve", "review_spec")
    .addEdge("solve", "review_physics")

    // Join: chief waits for all three.
    .addEdge(["review_layout", "review_spec", "review_physics"], "chief")

    .addConditionalEdges("chief", route, { revise: "revise", finalize: "finalize" })
    .addConditionalEdges("compile_failed", route, {
      revise: "revise",
      finalize: "finalize",
    })

    // The recurrent edge.
    .addEdge("revise", "compile")
    .addEdge("finalize", END)

  return graph.compile({ checkpointer: new MemorySaver() })
}

/** Stop when the board is accepted or the iteration budget is spent. */
function route(s: GraphState): "revise" | "finalize" {
  if (s.verdict?.pass) return "finalize"
  if (s.iteration + 1 >= s.maxIterations) return "finalize"
  return "revise"
}

function record(
  state: GraphState,
  verdict: Verdict,
  reviews: Record<string, Review>,
): IterationRecord {
  return {
    index: state.iteration,
    compiled: !state.build?.compileError,
    components: state.build?.components.length ?? 0,
    nets: state.build?.netlist.length ?? 0,
    peak_temperature_c: state.physics?.thermal?.peak_c,
    max_ir_drop_mv: state.physics?.rails.reduce((m, r) => Math.max(m, r.max_drop_mv), 0),
    drc_errors: state.physics?.geometry.filter((v) => v.severity === "error").length,
    // Every deterministic gate, not just L6. Counting only physics here made the
    // summary read `hard_failures: 0` on a board the chief had just blocked over three
    // L8 violations — a run record that disagrees with its own verdict is worse than no
    // record at all.
    hard_failures:
      (state.physics ? physicsBlockers(state.physics).length : 0) +
      (state.spice?.hardFailures.length ?? 0) +
      (state.dfm ? dfmBlockers(state.dfm).length : 0),
    spice_claims_passing: state.spice
      ? `${state.spice.claims.filter((c) => c.pass).length}/${state.spice.claims.length}`
      : undefined,
    spice_model_coverage_pct: state.spice ? Math.round(state.spice.coveragePercent) : undefined,
    dfm_errors: state.dfm?.errors,
    dfm_warnings: state.dfm?.warnings,
    findings: Object.fromEntries(
      Object.entries(reviews).map(([k, v]) => [k, v.findings.length]),
    ),
    work_order: verdict.work_order.length,
    pass: verdict.pass,
    summary: verdict.summary,
  }
}

export interface RunOptions extends GraphDeps {
  spec: string
  outDir: string
  maxIterations: number
  seedCode?: string
}

export async function runDesign(opts: RunOptions) {
  await fs.mkdir(opts.outDir, { recursive: true })
  await fs.writeFile(path.join(opts.outDir, "spec.md"), opts.spec)

  const app = buildGraph(opts)
  return app.invoke(
    {
      spec: opts.spec,
      outDir: opts.outDir,
      maxIterations: opts.maxIterations,
      iteration: 0,
      code: opts.seedCode ?? "",
    },
    {
      configurable: { thread_id: path.basename(opts.outDir) },
      // Each iteration is ~8 supersteps; leave headroom so the budget, not the
      // recursion guard, is what stops the loop.
      recursionLimit: opts.maxIterations * 12 + 12,
    },
  )
}
