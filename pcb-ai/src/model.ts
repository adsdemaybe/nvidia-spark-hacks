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
  if (spec.id === "stub") return new StubChatModel() as unknown as ChatLike

  const [provider, ...rest] = spec.id.split(":")
  if (!rest.length) {
    throw new Error(
      `Model must be given as provider:model (got "${spec.id}"). ` +
        `Examples: google-genai:gemini-3.7-flash, anthropic:claude-opus-5, openai:gpt-5, ollama:llama3.2, or stub.`,
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
    const key = `${spec.id}|${JSON.stringify(spec.kwargs ?? {})}`
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

/** Assemble a user turn from text sections and labelled images. */
export async function contentOf(
  sections: string[],
  images: Record<string, string> = {},
): Promise<ContentBlock[]> {
  const content: ContentBlock[] = [{ type: "text", text: sections.join("\n\n") }]
  for (const [name, file] of Object.entries(images)) {
    content.push({ type: "text", text: `${name}:` })
    content.push(await imageBlock(file))
  }
  return content
}

/** Pull the HDL out of a fenced code block, tolerating prose around it. */
export function extractCode(raw: string): string {
  const fenced = raw.match(/```(?:tsx?|jsx?|typescript)?\s*\n([\s\S]*?)```/)
  const code = (fenced ? fenced[1] : raw).trim()
  if (!code.includes("export default")) {
    throw new Error(`Model returned no default-exported component:\n${raw.slice(0, 500)}`)
  }
  return code
}
