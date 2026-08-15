# docs-rag

Retrieval over the **tscircuit** and **build123d** documentation, for the agents that
write `.tsx` boards (F1) and `build123d` parts (F2).

## Why it exists

A measured failure, not a feature idea. Asked for a two-LED indicator board, the model
wrote:

```tsx
<connector name="J1" pins="2">
  <connection pin="1" net="net.VCC" />
</connector>
```

Fluent, plausible, and not tscircuit. The real answer is `<pinheader pinCount={2} />`
with a separate `<trace>` per connection. `HDL_GUIDE` listed the element *names* but
showed one signature and never showed how components connect at all, so the model filled
the gap from React intuition and the lint caught it three stages later.

Writing that section by hand fixed those two cases. It does not scale: the guide cannot
carry every element's prop table, and the next unfamiliar element fails the same way.
This puts the real documentation for whatever a board actually uses in front of the model.

## How it retrieves

BM25 with three boosts, no embedding model:

| boost | why |
|---|---|
| exact **symbol** hit | `pinCount` in a chunk's declared identifiers beats any prose similarity |
| **path** stem match | `docs/elements/capacitor.mdx` *is* the capacitor docs; tutorials that place one are not |
| **prop table** | an element page splits into prose and `\| Property \| Type \|`; the table is the half with the answer |

Dense embeddings were considered and deliberately not used. The queries are identifier
lookups, and a general sentence embedder is trained to put `pins` and `pinCount` near
each other — exactly the distinction that must not blur. It also costs no GPU, which
matters on a box where Isaac Sim, the CAD service and the model servers already compete
for memory. `Retriever` takes an optional `dense` callable so one can be layered in for
conceptual paraphrase, which is where lexical is genuinely weaker.

## Use

```bash
./refresh.sh                                    # fetch + ingest + test
PYTHONPATH=src python -m uvicorn docsrag.server:app --port 8220
```

```
POST /search   {"query": "...", "source": "tscircuit"|"build123d"|null, "k": 5}
POST /context  same -> a prompt block inside a hard char budget
GET  /health
```

pcb-ai calls it from `src/docs-rag.ts`, **best-effort**: if this service is down, slow, or
has nothing to say, the design step runs exactly as it did before. A board that failed to
build because a docs service was unreachable would be a worse failure than the one this
fixes.

## Numbers

2163 chunks (1272 tscircuit, 891 build123d). Loads in 0.08 s, searches in under a
millisecond, and a full per-element retrieval for a 4-part board is ~7 kB (~1.8k tokens)
in 32 ms.
