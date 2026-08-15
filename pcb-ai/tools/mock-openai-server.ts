#!/usr/bin/env tsx
/**
 * A mock OpenAI-compatible endpoint — the offline stub, served over HTTP.
 *
 * Wiring a local model in has two failure surfaces, and they fail differently:
 *
 *   1. The transport — base URL resolution, auth headers, the OpenAI request/response
 *      shape, and how `withStructuredOutput` asks for schema-constrained JSON (tool
 *      call, or `response_format: json_schema`).
 *   2. The model — whether Laguna actually designs good boards.
 *
 * vLLM is not installed on this machine, so (2) cannot be tested yet. (1) can, and it is
 * the part that breaks silently: a base URL that resolves to nothing, a key header the
 * server rejects, or a structured-output method the server does not implement all look
 * like "the model gave a bad answer" from inside the pipeline.
 *
 * This server answers exactly like `src/models/stub.ts` — it reuses the same fixtures,
 * so there is one set of canned answers, not two — and records every request to
 * `--log`, which is how the run asserts that a text-only model was never sent an image.
 *
 *   npx tsx tools/mock-openai-server.ts --port 8000 --log /tmp/requests.jsonl
 */
import http from "node:http"
import fs from "node:fs"
import { parseArgs } from "node:util"
import { StubChatModel } from "../src/models/stub.ts"

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8000" },
    log: { type: "string" },
    model: { type: "string", default: "laguna" },
  },
})

const port = Number(values.port)
const stub = new StubChatModel()

function record(entry: unknown) {
  if (!values.log) return
  fs.appendFileSync(values.log, JSON.stringify(entry) + "\n")
}

/** Summarise a request's message content by block type, without dumping base64. */
function summarise(body: any) {
  const blocks: string[] = []
  for (const m of body.messages ?? []) {
    if (typeof m.content === "string") {
      blocks.push(`${m.role}:text(${m.content.length})`)
    } else if (Array.isArray(m.content)) {
      for (const b of m.content) {
        const type = b?.type ?? "unknown"
        const size =
          type === "text" ? (b.text?.length ?? 0) : (b.image_url?.url?.length ?? 0)
        blocks.push(`${m.role}:${type}(${size})`)
      }
    }
  }
  return blocks
}

/** The structured-call name, however this request asked for structure. */
function structuredName(body: any): string | undefined {
  const tool = body.tools?.[0]
  if (tool?.function?.name) return tool.function.name
  if (body.functions?.[0]?.name) return body.functions[0].name
  const rf = body.response_format
  if (rf?.type === "json_schema") return rf.json_schema?.name
  return undefined
}

const server = http.createServer((req, res) => {
  const chunks: Buffer[] = []
  req.on("data", (c) => chunks.push(c))
  req.on("end", async () => {
    const url = req.url ?? ""
    const send = (code: number, payload: unknown) => {
      const text = JSON.stringify(payload)
      res.writeHead(code, { "content-type": "application/json" })
      res.end(text)
    }

    if (req.method === "GET" && url.startsWith("/v1/models")) {
      return send(200, {
        object: "list",
        data: [{ id: values.model, object: "model", owned_by: "local" }],
      })
    }

    if (req.method !== "POST" || !url.startsWith("/v1/chat/completions")) {
      return send(404, { error: { message: `no route for ${req.method} ${url}` } })
    }

    let body: any
    try {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8"))
    } catch (err) {
      return send(400, { error: { message: `bad JSON: ${(err as Error).message}` } })
    }

    const name = structuredName(body)
    const images = summarise(body).filter((b) => b.includes("image")).length
    record({
      authorization: req.headers.authorization ?? null,
      model: body.model,
      structured_call: name ?? null,
      blocks: summarise(body),
      image_blocks: images,
    })

    const base = {
      id: "chatcmpl-mock",
      object: "chat.completion",
      created: 0,
      model: body.model ?? values.model,
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    }

    // Prose turn — the designer. The stub returns a fenced, compilable board.
    if (!name) {
      const answer = await stub.invoke([])
      return send(200, {
        ...base,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: String(answer.content) },
            finish_reason: "stop",
          },
        ],
      })
    }

    let payload: unknown
    try {
      payload = await stub.withStructuredOutput(null, { name }).invoke([])
    } catch (err) {
      // The stub throws on an unknown structured call by design — surface it as a
      // server error rather than a malformed completion, so the cause is obvious.
      return send(501, { error: { message: (err as Error).message } })
    }

    // Answer in whichever form the client asked for structure.
    if (body.response_format?.type === "json_schema") {
      return send(200, {
        ...base,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: JSON.stringify(payload) },
            finish_reason: "stop",
          },
        ],
      })
    }

    return send(200, {
      ...base,
      choices: [
        {
          index: 0,
          message: {
            role: "assistant",
            content: null,
            tool_calls: [
              {
                id: "call_mock",
                type: "function",
                function: { name, arguments: JSON.stringify(payload) },
              },
            ],
          },
          finish_reason: "tool_calls",
        },
      ],
    })
  })
})

server.listen(port, () => {
  console.log(`mock OpenAI endpoint on http://localhost:${port}/v1 (model "${values.model}")`)
  if (values.log) console.log(`recording requests to ${values.log}`)
})
