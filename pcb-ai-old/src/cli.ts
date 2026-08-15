#!/usr/bin/env tsx
import fs from "node:fs/promises"
import path from "node:path"
import { parseArgs } from "node:util"
import { z } from "zod"
import { runDesign } from "./graph.ts"
import {
  askStructured,
  buildRoster,
  resolveModel,
  ROLES,
  type ModelSpec,
  type RoleName,
} from "./model.ts"
import { encodePng } from "./physics/png.ts"
import type { OperatingPoint } from "./schemas.ts"

const USAGE = `
pcb-ai — design a PCB from a written spec, analyse the physics, and refine it in a loop.

  npm run design -- --spec <file>            design from a specification file
  npm run design -- --prompt "<text>"        design from an inline specification
  npm run design -- --seed <file.tsx>        start from existing HDL instead of generating

Models are given as provider:model and any LangChain chat provider works. The default
is Gemini, which reads GEMINI_API_KEY or GOOGLE_API_KEY:

  --model google-genai:gemini-3.7-flash      default — GA, strong on agentic work
  --model google-genai:gemini-3.1-pro-preview  flagship, 2M context, still preview
  --model google-genai:gemini-3.5-flash-lite   cheapest, for high-volume roles
  --model anthropic:claude-opus-5
  --model openai:gpt-5
  --model ollama:llama3.2
  --model stub                               offline fixture, no provider, no key

The reviewing roles are handed rendered images, so their model must be multimodal.
Every Gemini model is. Per-role overrides let you put a strong model on the design work
and a cheap one on the reading:

  --model-designer google-genai:gemini-3.1-pro-preview \\
  --model-spec google-genai:gemini-3.5-flash-lite

Roles: ${ROLES.join(", ")}

Options:
  --iterations <n>       analyse/review/revise rounds, default 3
  --model <id>           provider:model for every role, default google-genai:gemini-3.7-flash
  --model-<role> <id>    override one role
  --model-kwargs <json>  provider-specific extras, e.g. '{"temperature":0.2}'
  --out <dir>            output directory, default runs/<timestamp>
  --operating-point <f>  JSON operating point to analyse against, instead of asking
                         the modelling agent for one
  --check                verify each configured model answers, then exit
`.trim()

const { values } = parseArgs({
  options: {
    spec: { type: "string" },
    prompt: { type: "string" },
    seed: { type: "string" },
    iterations: { type: "string", default: "3" },
    model: { type: "string", default: "google-genai:gemini-3.7-flash" },
    "model-kwargs": { type: "string" },
    "model-parts": { type: "string" },
    "model-designer": { type: "string" },
    "model-modeler": { type: "string" },
    "model-physicist": { type: "string" },
    "model-layout": { type: "string" },
    "model-spec": { type: "string" },
    "model-chief": { type: "string" },
    out: { type: "string" },
    "operating-point": { type: "string" },
    check: { type: "boolean", default: false },
    help: { type: "boolean", default: false },
  },
})

if (values.help || (!values.check && !values.spec && !values.prompt && !values.seed)) {
  console.log(USAGE)
  process.exit(values.help ? 0 : 1)
}

const spec = values.spec
  ? await fs.readFile(values.spec, "utf8")
  : (values.prompt ??
    "Build the board described by the seed HDL. Judge it as a general-purpose design.")

const seedCode = values.seed ? await fs.readFile(values.seed, "utf8") : undefined

const kwargs = values["model-kwargs"] ? JSON.parse(values["model-kwargs"]) : undefined
const fallback: ModelSpec = { id: values.model!, kwargs }
const overrides: Partial<Record<RoleName, ModelSpec>> = {}
for (const role of ROLES) {
  const id = (values as unknown as Record<string, string | undefined>)[`model-${role}`]
  if (id) overrides[role] = { id, kwargs }
}

const fixedOperatingPoint: OperatingPoint | undefined = values["operating-point"]
  ? JSON.parse(await fs.readFile(values["operating-point"], "utf8"))
  : undefined

/**
 * Preflight: one call per distinct model, exercising the three things the pipeline
 * depends on — auth, schema-constrained output, and vision. Cheaper to find out here
 * than four nodes into a run.
 */
if (values.check) {
  const specs = new Map<string, ModelSpec>()
  for (const role of ROLES) {
    const s = overrides[role] ?? fallback
    if (!specs.has(s.id)) specs.set(s.id, s)
  }

  // 8x8 solid red, so a vision failure is unambiguous.
  const rgba = new Uint8Array(8 * 8 * 4)
  for (let i = 0; i < 8 * 8; i++) rgba.set([220, 32, 32, 255], i * 4)
  const swatch = encodePng(8, 8, rgba).toString("base64")

  const Probe = z.object({
    ok: z.boolean().describe("Always true."),
    colour: z.string().describe("The dominant colour of the image, one word."),
  })

  let failed = false
  for (const [id, spec] of specs) {
    process.stdout.write(`check     ${id} … `)
    try {
      const model = await resolveModel(spec)
      const answer = await askStructured<{ ok: boolean; colour: string }>(
        model,
        Probe,
        "probe",
        "You are a preflight check. Answer from the image.",
        [
          { type: "text", text: "What colour is this image?" },
          { type: "image", mimeType: "image/png", data: swatch },
        ],
      )
      console.log(`ok (structured output + vision: saw "${answer.colour}")`)
    } catch (err) {
      failed = true
      console.log(`FAILED\n            ${(err as Error).message.split("\n")[0]}`)
    }
  }
  process.exit(failed ? 1 : 0)
}

const models = await buildRoster(fallback, overrides)

const stamp = new Date().toISOString().replace(/[:.]/g, "-")
const outDir = values.out ?? path.join("runs", stamp)

const used = new Set(
  ROLES.map((r) => (overrides[r] ?? fallback).id),
)
console.log(`models    ${[...used].join(", ")}`)

await runDesign({
  spec,
  seedCode,
  maxIterations: Number(values.iterations),
  outDir,
  models,
  fixedOperatingPoint,
})
