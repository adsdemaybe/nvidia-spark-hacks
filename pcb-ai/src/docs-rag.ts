/**
 * Client for the docs retriever (`rag/`, port 8220).
 *
 * The reason this exists is a measured failure, not a feature idea. Given a spec, the
 * model wrote `<connector>` wrapping `<connection pin="1" net="net.VCC" />`, and on the
 * next run `<pinheader pins="2">`. Both are fluent, plausible, and not tscircuit: the
 * real answer is `<pinheader pinCount={2} />` with a separate `<trace>` per connection.
 * `HDL_GUIDE` listed the element names but showed one signature and never showed how
 * components connect at all, so the model filled the gap from React intuition.
 *
 * Writing that section by hand fixed those two cases. It does not scale — the guide
 * cannot carry every element's prop table, and the next unfamiliar element fails the same
 * way. Retrieval puts the actual documentation for whatever the board uses in front of
 * the model instead.
 *
 * **Never fatal.** The retriever is an improvement to a prompt, not a dependency of the
 * pipeline. If it is down, slow, or has nothing to say, the design step runs exactly as
 * it did before with the hand-written guide. A board that fails to build because a docs
 * service was unreachable would be a worse failure than the one this fixes.
 */

const DEFAULT_URL = process.env.DOCS_RAG_URL ?? "http://127.0.0.1:8220"

/** Short, because a prompt that waits on a search has already lost the argument. */
const TIMEOUT_MS = Number(process.env.DOCS_RAG_TIMEOUT_MS ?? 4000)

export interface RagOptions {
  source?: "tscircuit" | "build123d"
  k?: number
  budgetChars?: number
  url?: string
}

/**
 * Retrieved documentation as a prompt block, or `""` when there is none.
 *
 * Empty rather than a low-confidence guess: a block that always contains something
 * teaches the model it is background noise, and one that is absent when there is nothing
 * to say keeps it meaning "this is the documentation, and it outranks your recollection".
 */
export async function retrieveDocs(query: string, opts: RagOptions = {}): Promise<string> {
  const url = opts.url ?? DEFAULT_URL
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(`${url}/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        source: opts.source ?? null,
        k: opts.k ?? 4,
        budget_chars: opts.budgetChars ?? 5000,
      }),
      signal: ctl.signal,
    })
    if (!res.ok) return ""
    const data = (await res.json()) as { context?: string }
    return data.context ?? ""
  } catch {
    // Unreachable, timed out, or returned nonsense — all the same to the caller.
    return ""
  } finally {
    clearTimeout(timer)
  }
}

/**
 * The element kinds a parts plan implies, from its reference designators.
 *
 * `trace` and `net` are always included: connectivity is the thing models get wrong and
 * no reference designator implies it.
 */
export function kindsForParts(parts: Array<{ ref: string }>): string[] {
  const kinds = new Set<string>(["trace", "net"])
  for (const p of parts) {
    const letters = (p.ref.match(/^[A-Za-z]+/) ?? [""])[0].toUpperCase()
    const kind = REF_PREFIX[letters]
    if (kind) kinds.add(kind)
  }
  return [...kinds]
}

/**
 * One retrieval per element kind, merged.
 *
 * A single query naming every element is the obvious approach and it measurably does not
 * work: "pinheader connector resistor led capacitor trace net props and examples" scores
 * highest on pages that mention many elements shallowly — overviews and quickstarts —
 * and returned 1677 characters containing no `pinCount` at all. BM25 spreads its mass
 * across the terms, so the more elements a board has, the *less* likely any one of them
 * is documented in the result.
 *
 * Asking separately gives each element its own budget and its own top hit, which is what
 * the model actually needs: the props table for every element it is about to write. The
 * requests go out together, so the cost is one round trip.
 */
export async function retrieveForKinds(
  kinds: string[],
  opts: RagOptions & { perKindChars?: number } = {},
): Promise<string> {
  const perKind = opts.perKindChars ?? 1500
  const budget = opts.budgetChars ?? 6000
  const results = await Promise.all(
    kinds.map((kind) =>
      // Two chunks, not one: an element page splits into an intro and a
      // `| Property | Type |` table, and the intro alone wins on score while the table is
      // the half that stops a model inventing prop names.
      retrieveDocs(`${kind} element props and example`, {
        ...opts,
        k: 2,
        budgetChars: perKind,
      }).then((text) => ({ kind, text })),
    ),
  )

  const seen = new Set<string>()
  const parts: string[] = []
  let used = 0
  for (const { text } of results) {
    if (!text) continue
    // Each call returns a wrapped block; unwrap so the result has one wrapper, not N.
    const inner = text.replace(/^<docs-retrieved>[\s\S]*?\n\n/, "").replace(/\n<\/docs-retrieved>$/, "")
    const key = inner.slice(0, 120)
    if (seen.has(key)) continue // two kinds often share a page
    if (used + inner.length > budget) continue
    seen.add(key)
    parts.push(inner)
    used += inner.length
  }
  if (!parts.length) return ""
  return (
    "<docs-retrieved>\n" +
    "Authoritative extracts from the real tscircuit documentation, one per element this " +
    "board uses. Where these disagree with your recollection of the API, these are right.\n\n" +
    parts.join("\n\n") +
    "\n</docs-retrieved>"
  )
}

/** Reference-designator conventions, mapped to tscircuit element names. */
const REF_PREFIX: Record<string, string> = {
  R: "resistor",
  C: "capacitor",
  L: "inductor",
  D: "led diode",
  Q: "transistor mosfet",
  U: "chip",
  // `pinheader`, not "pinheader connector". Both elements exist, but the two-word form
  // let the path boost pick connector.mdx and the retrieved docs then never contained
  // `pinCount` — the one token this is here to supply. HDL_GUIDE tells the model to
  // prefer pinheader; the retrieval has to agree with the guide or they fight.
  J: "pinheader",
  SW: "switch pushbutton",
  Y: "crystal",
  X: "crystal",
  F: "fuse",
  TP: "testpoint",
  MH: "hole",
  B: "battery",
}
