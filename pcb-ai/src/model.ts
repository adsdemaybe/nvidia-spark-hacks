/**
 * Provider-agnostic model layer.
 *
 * Every agent talks to a `ChatLike`, which is the slice of LangChain's chat-model
 * interface this pipeline uses: `invoke` for prose and `withStructuredOutput` for
 * schema-constrained JSON. `BaseChatModel` satisfies it structurally, so any of
 * LangChain's providers works, and so does the offline stub used for testing the
 * graph without a key.
 */
import fs from "node:fs/promises"
import path from "node:path"
import { initChatModel } from "langchain/chat_models/universal"
import { ChatOpenAI } from "@langchain/openai"
import { HumanMessage, SystemMessage, type BaseMessage } from "@langchain/core/messages"
import type { z } from "zod"
import { StubChatModel } from "./models/stub.ts"

/** A multimodal user-turn block in LangChain's standard content format. */
export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; mimeType: string; data: string }

export interface ChatLike {
  invoke(input: BaseMessage[]): Promise<{ content: unknown }>
  withStructuredOutput<T>(
    schema: unknown,
    config?: Record<string, unknown>,
  ): { invoke(input: BaseMessage[]): Promise<T> }
}

export interface ModelSpec {
  /** `provider:model`, e.g. `google-genai:gemini-3.7-flash`, `openai:gpt-5`. */
  id: string
  /** Provider-specific extras passed straight through, e.g. thinking or reasoning config. */
  kwargs?: Record<string, unknown>
  /** Base URL for a locally-served OpenAI-compatible endpoint (`laguna`, `local`). */
  baseUrl?: string
  /** Extra request-body fields, merged over the endpoint's own defaults. */
  extraBody?: Record<string, unknown>
  /**
   * Request timeout, milliseconds.
   *
   * Hosted providers answer in seconds and the SDK's default is tuned for them. A local
   * 27B on this box generates at roughly 4 tok/s, so a reviewer reading a 25 kB prompt
   * and writing a few hundred tokens can legitimately take many minutes — and the
   * default cut it off mid-review with `TimeoutError: Request timed out`, which reads
   * like the model failed rather than like the client gave up.
   *
   * Generous rather than unbounded: a request that really has hung should still end.
   */
  timeoutMs?: number
  /**
   * Retries on failure.
   *
   * One, not the default two. A retry against a model this slow costs as much as the
   * original call, so a genuinely stuck request would burn three timeouts before
   * surfacing — the failure arrives so late it is hard to attribute.
   */
  maxRetries?: number
  /**
   * Cap on generated tokens.
   *
   * Without one the server's only stop is the 32k context limit, and at ~4 tok/s that is
   * over two hours — so the *timeout* became the de-facto stop condition. That is the
   * worst way to bound a generation: the request is cut mid-token, the SDK raises
   * `TimeoutError`, and the whole iteration is discarded having written nothing. The
   * ai-indicator run failed exactly this way, producing no output at all in 30 minutes.
   *
   * A cap turns that into a *finished* response with `finish_reason: "length"` — still a
   * failure, but a legible one that arrives with its partial work and can be attributed
   * to a stage. `extractCode()` already detects precisely this truncation. Sized so the
   * worst case lands inside `timeoutMs`: 6000 tokens at 4 tok/s is 25 minutes against a
   * 30-minute budget.
   */
  maxTokens?: number
}

/**
 * Which environment variables each provider's key may live in, most conventional
 * first.
 *
 * Google is the reason this table exists: Google's own docs and SDKs use
 * `GEMINI_API_KEY`, while `@langchain/google-genai` only ever reads `GOOGLE_API_KEY`.
 * Anyone following Google's quickstart would otherwise hit "API key not found" with
 * their key sitting right there in the environment. Resolving it here and passing
 * `apiKey` explicitly makes either name work.
 */
const API_KEY_ENV: Record<string, string[]> = {
  "google-genai": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  google: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  anthropic: ["ANTHROPIC_API_KEY"],
  openai: ["OPENAI_API_KEY"],
  azure_openai: ["AZURE_OPENAI_API_KEY"],
  mistralai: ["MISTRAL_API_KEY"],
  groq: ["GROQ_API_KEY"],
  cohere: ["COHERE_API_KEY"],
  deepseek: ["DEEPSEEK_API_KEY"],
  xai: ["XAI_API_KEY"],
  together: ["TOGETHER_AI_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  cerebras: ["CEREBRAS_API_KEY"],
  perplexity: ["PERPLEXITY_API_KEY"],
}

/** Providers that authenticate some other way, or not at all. */
const NO_API_KEY = new Set(["ollama", "bedrock", "aws", "google-vertexai", "google-vertexai-web"])

/**
 * Locally-served, OpenAI-compatible endpoints.
 *
 * Laguna S 2.1 NVFP4 runs on the Spark under vLLM (`setup/serve_laguna.sh`), which
 * speaks the OpenAI wire format. So it needs no provider implementation of its own —
 * only a base URL, a throwaway key (the server wants the header, not the value), and an
 * honest statement of what it cannot do.
 *
 * `vision: false` is the load-bearing part. Laguna is a coding model; the physicist,
 * layout and spec reviewers are handed rendered PNGs. Sending images to a text-only
 * endpoint does not degrade gracefully — it either errors or, worse, the model answers
 * confidently about an image it never received. Declaring the capability here lets
 * `contentOf` substitute a deterministic textual digest instead (see `layout-digest.ts`).
 * Override with `LAGUNA_VISION=1` if a multimodal model is served on the same endpoint.
 */
interface LocalEndpoint {
  envBaseUrl: string[]
  defaultBaseUrl: string
  defaultModel: string
  envModel: string[]
  envKey: string[]
  envVision: string
  vision: boolean
  /**
   * Extra fields merged into the request body, for server switches the OpenAI schema
   * has no place for.
   *
   * Qwen3 needs this. It is a reasoning model whose chat template opens `<think>`
   * implicitly, so the trace arrives inside `content` with only a closing tag — and it
   * is expensive. Measured on this box, same question: 207 completion tokens in 59 s
   * with reasoning, 98 tokens in 26 s without. Across seven agent calls per iteration
   * that is the difference between minutes and tens of minutes.
   *
   * Off by default because the deterministic ladder owns correctness — a weaker
   * reviewer degrades the advice and cannot change a verdict (plan §8.7) — so iteration
   * speed is worth more than marginally better prose. `--think` puts it back.
   */
  extraBody?: Record<string, unknown>
  /**
   * Request timeout, milliseconds.
   *
   * Hosted providers answer in seconds and the SDK's default is tuned for them. A local
   * 27B on this box generates at roughly 4 tok/s, so a reviewer reading a 25 kB prompt
   * and writing a few hundred tokens can legitimately take many minutes — and the
   * default cut it off mid-review with `TimeoutError: Request timed out`, which reads
   * like the model failed rather than like the client gave up.
   *
   * Generous rather than unbounded: a request that really has hung should still end.
   */
  timeoutMs?: number
  /**
   * Retries on failure.
   *
   * One, not the default two. A retry against a model this slow costs as much as the
   * original call, so a genuinely stuck request would burn three timeouts before
   * surfacing — the failure arrives so late it is hard to attribute.
   */
  maxRetries?: number
  /**
   * Cap on generated tokens.
   *
   * Without one the server's only stop is the 32k context limit, and at ~4 tok/s that is
   * over two hours — so the *timeout* became the de-facto stop condition. That is the
   * worst way to bound a generation: the request is cut mid-token, the SDK raises
   * `TimeoutError`, and the whole iteration is discarded having written nothing. The
   * ai-indicator run failed exactly this way, producing no output at all in 30 minutes.
   *
   * A cap turns that into a *finished* response with `finish_reason: "length"` — still a
   * failure, but a legible one that arrives with its partial work and can be attributed
   * to a stage. `extractCode()` already detects precisely this truncation. Sized so the
   * worst case lands inside `timeoutMs`: 6000 tokens at 4 tok/s is 25 minutes against a
   * 30-minute budget.
   */
  maxTokens?: number
}

const LOCAL_ENDPOINTS: Record<string, LocalEndpoint> = {
  laguna: {
    envBaseUrl: ["LAGUNA_BASE_URL", "LLM_BASE_URL"],
    // 8100 / laguna-nvfp4 is where `serve_laguna.sh` actually puts the real model on
    // this Spark. Port 8000 has historically hosted the mock fixture server, so
    // defaulting there points a run at canned answers that look like a working model —
    // the worst possible default.
    defaultBaseUrl: "http://localhost:8100/v1",
    defaultModel: "laguna-nvfp4",
    envModel: ["LAGUNA_MODEL", "LLM_MODEL"],
    envKey: ["LAGUNA_API_KEY", "LLM_API_KEY"],
    envVision: "LAGUNA_VISION",
    vision: false,
    timeoutMs: 30 * 60_000,
    maxRetries: 1,
    maxTokens: 6000,
  },
  // The GPU tier as of 2026-08-15: Qwen3.8-27B replaced Laguna S 2.1.
  //
  // The pivot was about memory, not quality. Laguna's NVFP4 checkpoint is 93 GB and vLLM
  // held 97-117 GB of the GB10's 121 GB pool, which left nothing for Isaac Sim, gsplat or
  // a fine-tune — and lowering gpu-memory-utilization frees almost nothing because the
  // weights are the floor. This one runs at 0.75 utilisation in ~53 GB, at a 32k context
  // rather than 16k, which is the difference between a designer turn fitting and being
  // truncated mid-file (plan §8.8 measured that turn at 14.3k tokens).
  qwen3: {
    envBaseUrl: ["QWEN3_BASE_URL", "LLM_BASE_URL"],
    defaultBaseUrl: "http://localhost:8100/v1",
    defaultModel: "qwen3.8-27b",
    envModel: ["QWEN3_MODEL"],
    envKey: ["QWEN3_API_KEY", "LLM_API_KEY"],
    envVision: "QWEN3_VISION",
    vision: false,
    extraBody: { chat_template_kwargs: { enable_thinking: false } },
    timeoutMs: 30 * 60_000,
    maxRetries: 1,
    maxTokens: 6000,
  },
  // The CPU tier. llama.cpp on the Spark's ARM cores, holding **zero GPU memory** — so
  // the design loop keeps running while Isaac Sim, gsplat or a fine-tune owns the GB10.
  // Weaker than Laguna at review (§8.7 in the plan) and unable to degrade any verdict,
  // because the deterministic gates do not care which model is talking.
  qwen: {
    envBaseUrl: ["QWEN_BASE_URL", "LLM_BASE_URL"],
    defaultBaseUrl: "http://127.0.0.1:8200/v1",
    defaultModel: "qwen",
    envModel: ["QWEN_MODEL"],
    envKey: ["QWEN_API_KEY", "LLM_API_KEY"],
    envVision: "QWEN_VISION",
    vision: false,
    timeoutMs: 30 * 60_000,
    maxRetries: 1,
    maxTokens: 6000,
  },
  // Generic escape hatch for any other locally-served OpenAI-compatible model
  // (a second vLLM, llama.cpp, TGI). `--model local:my-model --base-url ...`.
  local: {
    envBaseUrl: ["LOCAL_BASE_URL", "LLM_BASE_URL"],
    defaultBaseUrl: "http://localhost:8000/v1",
    defaultModel: "default",
    envModel: ["LOCAL_MODEL"],
    envKey: ["LOCAL_API_KEY", "LLM_API_KEY"],
    envVision: "LOCAL_VISION",
    vision: false,
    timeoutMs: 30 * 60_000,
    maxRetries: 1,
    maxTokens: 6000,
  },
}

/** First of these environment variables that is actually set. */
function firstEnv(names: string[]): string | undefined {
  for (const name of names) {
    const value = process.env[name]
    if (value) return value
  }
  return undefined
}

/**
 * What a resolved model can actually do.
 *
 * Kept beside the instance rather than on it: `ChatLike` is a structural type satisfied
 * by LangChain's own classes, and bolting properties onto a provider's object is the
 * kind of thing that breaks on a minor version bump.
 */
export interface ModelCapabilities {
  id: string
  vision: boolean
}

const CAPABILITIES = new WeakMap<object, ModelCapabilities>()

export function capabilitiesOf(model: ChatLike): ModelCapabilities {
  return CAPABILITIES.get(model as object) ?? { id: "unknown", vision: true }
}

/** Whether this model may be handed image content blocks. */
export function supportsVision(model: ChatLike): boolean {
  return capabilitiesOf(model).vision
}

/** First environment variable that is actually set for this provider, if any. */
function resolveApiKey(provider: string): { key?: string; checked: string[] } {
  const checked = API_KEY_ENV[provider] ?? []
  for (const name of checked) {
    const value = process.env[name]
    if (value) return { key: value, checked }
  }
  return { checked }
}

/**
 * Resolve a model spec to something callable.
 *
 * `stub` is handled here rather than in the graph so the pipeline has exactly one
 * code path whether it is running against a real provider or offline.
 */
export async function resolveModel(spec: ModelSpec): Promise<ChatLike> {
  if (spec.id === "stub") {
    const stub = new StubChatModel() as unknown as ChatLike
    CAPABILITIES.set(stub as object, { id: "stub", vision: true })
    return stub
  }

  const [provider, ...rest] = spec.id.split(":")

  // Locally-served OpenAI-compatible endpoints resolve before the provider:model rule,
  // so a bare `--model laguna` works — there is only ever one model on that endpoint.
  const endpoint = LOCAL_ENDPOINTS[provider]
  if (endpoint) {
    const baseURL = spec.baseUrl ?? firstEnv(endpoint.envBaseUrl) ?? endpoint.defaultBaseUrl
    const modelName = rest.join(":") || firstEnv(endpoint.envModel) || endpoint.defaultModel
    const apiKey = firstEnv(endpoint.envKey) ?? "local"
    const visionEnv = process.env[endpoint.envVision]
    const vision = visionEnv === undefined ? endpoint.vision : visionEnv === "1" || visionEnv === "true"

    // spec.extraBody wins over the endpoint default, so --think can re-enable reasoning
    // without editing this table.
    const extraBody = { ...(endpoint.extraBody ?? {}), ...(spec.extraBody ?? {}) }

    // Constructed directly rather than through `initChatModel`.
    //
    // `initChatModel` forwards only the options it recognises, and silently drops the
    // rest — `timeout`, `maxRetries` and `modelKwargs` all arrived at the provider as
    // `undefined`. Nothing errored: requests simply kept the SDK defaults, so the
    // reasoning-off switch never reached the server and every run that claimed to have
    // it was quietly reasoning anyway, and the long timeout for slow local models was
    // never applied. A dropped option is the worst kind of misconfiguration because the
    // code reads as if it took effect.
    //
    // Hosted providers still go through `initChatModel` below — twenty-odd of them
    // resolve through one call and none of them need these knobs.
    const model = new ChatOpenAI({
      model: modelName,
      apiKey,
      configuration: { baseURL },
      ...(endpoint.timeoutMs ? { timeout: endpoint.timeoutMs } : {}),
      ...(endpoint.maxRetries !== undefined ? { maxRetries: endpoint.maxRetries } : {}),
      ...(endpoint.maxTokens ? { maxTokens: endpoint.maxTokens } : {}),
      ...(Object.keys(extraBody).length ? { modelKwargs: extraBody } : {}),
      ...spec.kwargs,
    })
    CAPABILITIES.set(model as object, { id: `${provider}:${modelName} @ ${baseURL}`, vision })
    return model as unknown as ChatLike
  }

  if (!rest.length) {
    throw new Error(
      `Model must be given as provider:model (got "${spec.id}"). ` +
        `Examples: google-genai:gemini-3.7-flash, anthropic:claude-opus-5, openai:gpt-5, ollama:llama3.2, laguna, or stub.`,
    )
  }

  const { key, checked } = resolveApiKey(provider)
  if (!key && !NO_API_KEY.has(provider) && checked.length) {
    throw new Error(
      `No API key for provider "${provider}". Set ${checked.join(" or ")} and try again.` +
        `\nTo run the whole pipeline with no provider at all, use --model stub.`,
    )
  }

  const model = await initChatModel(rest.join(":"), {
    modelProvider: provider,
    ...(key ? { apiKey: key } : {}),
    ...spec.kwargs,
  })
  // Hosted providers in this roster are multimodal; the local ones above are the
  // exception, and they say so explicitly.
  CAPABILITIES.set(model as object, { id: spec.id, vision: true })
  return model as unknown as ChatLike
}

/**
 * Per-role model selection. Reviewing agents are handed images, so their model must
 * be multimodal; the designer only ever sees and emits text.
 */
export interface ModelRoster {
  parts: ChatLike
  designer: ChatLike
  modeler: ChatLike
  physicist: ChatLike
  layout: ChatLike
  spec: ChatLike
  chief: ChatLike
}

export type RoleName = keyof ModelRoster

export const ROLES: RoleName[] = [
  "parts",
  "designer",
  "modeler",
  "physicist",
  "layout",
  "spec",
  "chief",
]

/** Build the roster from one default spec plus optional per-role overrides. */
export async function buildRoster(
  fallback: ModelSpec,
  overrides: Partial<Record<RoleName, ModelSpec>> = {},
): Promise<ModelRoster> {
  // Resolve each distinct spec once and share the instance across the roles using it.
  const cache = new Map<string, Promise<ChatLike>>()
  const get = (spec: ModelSpec) => {
    const key = `${spec.id}|${spec.baseUrl ?? ""}|${JSON.stringify(spec.kwargs ?? {})}`
    if (!cache.has(key)) cache.set(key, resolveModel(spec))
    return cache.get(key)!
  }
  const entries = await Promise.all(
    ROLES.map(async (role) => [role, await get(overrides[role] ?? fallback)] as const),
  )
  return Object.fromEntries(entries) as unknown as ModelRoster
}

function toMessages(system: string, content: ContentBlock[] | string): BaseMessage[] {
  return [
    new SystemMessage(system),
    new HumanMessage({ content: content as never }),
  ]
}

/** Prose completion. */
export async function askText(
  model: ChatLike,
  system: string,
  content: ContentBlock[] | string,
): Promise<string> {
  const response = await model.invoke(toMessages(system, content))
  const raw = response.content
  if (typeof raw === "string") return raw
  if (Array.isArray(raw)) {
    return raw
      .filter((b: any) => b?.type === "text" && typeof b.text === "string")
      .map((b: any) => b.text)
      .join("")
  }
  return String(raw ?? "")
}

/**
 * Schema-constrained completion.
 *
 * `name` is what the stub keys its canned responses off, and what providers that
 * implement structured output through function calling use as the tool name.
 */
export async function askStructured<T>(
  model: ChatLike,
  schema: z.ZodType<T>,
  name: string,
  system: string,
  content: ContentBlock[] | string,
): Promise<T> {
  return model
    .withStructuredOutput<T>(schema, { name })
    .invoke(toMessages(system, content))
}

const MIME_BY_EXT: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
}

/** Read a rendered view off disk as a multimodal content block. */
export async function imageBlock(file: string): Promise<ContentBlock> {
  const data = await fs.readFile(file)
  return {
    type: "image",
    mimeType: MIME_BY_EXT[path.extname(file).toLowerCase()] ?? "image/png",
    data: data.toString("base64"),
  }
}

/**
 * Assemble a user turn from text sections and labelled images.
 *
 * `vision: false` is not "drop the images and hope". A reviewer that silently loses its
 * primary evidence would still answer, and its answer would be invented. So the images
 * are replaced by `digest` — a deterministic description of the same geometry, measured
 * from Circuit JSON — and the turn states plainly which views were withheld, so the
 * model does not claim to have looked at a picture.
 */
export async function contentOf(
  sections: string[],
  images: Record<string, string> = {},
  opts: { vision?: boolean; digest?: string } = {},
): Promise<ContentBlock[]> {
  const vision = opts.vision ?? true
  const names = Object.keys(images)

  if (!vision) {
    const text = [
      ...sections,
      names.length
        ? `<views-not-available>\nYou are a text-only model, so the rendered views ` +
          `(${names.join(", ")}) were not attached. Do not describe them or claim to ` +
          `have seen them. The measured geometry below is derived from the same ` +
          `Circuit JSON those views are drawn from, and is the evidence you have.\n` +
          `</views-not-available>`
        : "",
      opts.digest ? `<measured-geometry>\n${opts.digest}\n</measured-geometry>` : "",
    ]
      .filter(Boolean)
      .join("\n\n")
    return [{ type: "text", text }]
  }

  const content: ContentBlock[] = [{ type: "text", text: sections.join("\n\n") }]
  for (const [name, file] of Object.entries(images)) {
    content.push({ type: "text", text: `${name}:` })
    content.push(await imageBlock(file))
  }
  return content
}

/**
 * Pull the HDL out of a model's reply.
 *
 * The naive version of this — match a fenced block, else use the whole string — fails
 * in exactly the way that wastes an iteration. A reasoning model that runs out of
 * output budget mid-file emits an opening fence and never closes it, the match fails,
 * the raw reply is written to disk as HDL, and the next thing anyone sees is
 * `Parsing error: Declaration or statement expected` at line 1 on a stray `</think>`.
 * The linter is right and the message is useless: the problem is not the syntax, it is
 * that the file is half a file.
 *
 * So truncation is detected and named. A revision that was cut short is a *retryable
 * transport failure*, not a design mistake, and it should never reach the linter
 * pretending to be one.
 */
export function extractCode(raw: string): string {
  // Reasoning traces are not code. Strip well-formed blocks, and also the orphan
  // closing tag a truncated trace leaves behind.
  const cleaned = raw
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/^[\s\S]*?<\/think>/, "")
    .trim()

  const opening = cleaned.match(/```(?:tsx?|jsx?|typescript)?[ \t]*\r?\n/)
  if (opening) {
    const start = opening.index! + opening[0].length
    const closing = cleaned.indexOf("```", start)
    if (closing === -1) {
      throw new Error(
        "Model output was cut off: a code fence was opened and never closed, so the " +
          "revision is incomplete. This is a token-budget failure, not a design error — " +
          "raise the server's --max-model-len / max output tokens, or have the designer " +
          "emit a diff rather than the whole file.\n" +
          `Received ${cleaned.length} chars, last line: ${JSON.stringify(
            cleaned.split("\n").pop()?.slice(0, 120) ?? "",
          )}`,
      )
    }
    const code = cleaned.slice(start, closing).trim()
    if (!code.includes("export default")) {
      throw new Error(`Model returned no default-exported component:\n${code.slice(0, 500)}`)
    }
    return code
  }

  const code = cleaned
  if (!code.includes("export default")) {
    throw new Error(`Model returned no default-exported component:\n${raw.slice(0, 500)}`)
  }
  // Unfenced output that opens a JSX tree and never closes it is the same truncation in
  // a different costume. Balance is a cheap, false-negative-only signal: it can miss a
  // truncation, but a real imbalance is always a broken file.
  const opens = (code.match(/[({[]/g) ?? []).length
  const closes = (code.match(/[)}\]]/g) ?? []).length
  if (opens - closes > 2) {
    throw new Error(
      `Model output looks truncated: ${opens} opening brackets against ${closes} closing. ` +
        "Treating this as an incomplete revision rather than sending it to the linter.",
    )
  }
  return code
}
