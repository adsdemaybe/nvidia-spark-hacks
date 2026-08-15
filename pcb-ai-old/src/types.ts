/** Shared types for the PCB design pipeline. */
import type { PhysicsReport } from "./physics/index.ts"

/** A DRC / ERC finding pulled out of the Circuit JSON the compiler emitted. */
export interface CircuitFinding {
  /** Circuit JSON element type, e.g. `pcb_trace_missing_error`. */
  type: string
  severity: "error" | "warning"
  message: string
}

/** One net: a named connection and every component pin sitting on it. */
export interface NetSummary {
  name: string
  connections: string[]
}

/** Everything the compiler produced for one revision of the HDL source. */
export interface BuildResult {
  ok: boolean
  /** Set when the HDL failed to evaluate at all (syntax error, bad import, throw). */
  compileError?: string
  circuitJson: any[]
  findings: CircuitFinding[]
  netlist: NetSummary[]
  components: Array<{ name: string; type: string; footprint?: string; value?: string }>
  board?: { width: number; height: number; layerCount: number }
  /** Absolute paths of the rendered views, keyed by view name. */
  images: Record<string, string>
}

export type Severity = "blocker" | "major" | "minor"

export type Category =
  | "connectivity"
  | "placement"
  | "routing"
  | "footprint"
  | "electrical"
  | "thermal"
  | "power-integrity"
  | "manufacturability"
  | "spec-compliance"
  | "silkscreen"

/** One issue, from whichever reviewer raised it. */
export interface Finding {
  severity: Severity
  category: Category
  description: string
  /** Concrete change to make in the HDL. */
  suggested_fix: string
  /** Which reviewer raised it — carried through so the work order stays attributable. */
  source: string
}

/** What each reviewing agent returns. */
export interface ReviewOutput {
  summary: string
  findings: Array<Omit<Finding, "source">>
}

/** The chief engineer's decision for one iteration. */
export interface Verdict {
  pass: boolean
  summary: string
  /** Deduplicated, prioritised findings for the designer to act on. */
  work_order: Finding[]
}

/** One trip around the loop. */
export interface Iteration {
  index: number
  code: string
  build: BuildResult
  physics?: PhysicsReport
  reviews: Record<string, ReviewOutput>
  verdict: Verdict
  dir: string
}

export interface PipelineOptions {
  spec: string
  maxIterations: number
  model: string
  effort: "low" | "medium" | "high" | "xhigh" | "max"
  outDir: string
  /** Skip the model entirely and just build/render/analyse (harness test). */
  seedCode?: string
  dryRun?: boolean
  /** Operating point to use instead of asking the modelling agent for one. */
  operatingPointFile?: string
}
