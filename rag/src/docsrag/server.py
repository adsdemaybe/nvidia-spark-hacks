"""HTTP front end for the docs retriever.

One service, both features. pcb-ai is TypeScript and cad-generation is Python, so a
shared library is not on the table — an HTTP boundary is the same choice already made for
the CAD API on :8210 and for pcb-ai's own service, and for the same reason.

    uvicorn docsrag.server:app --host 127.0.0.1 --port 8220

    POST /search    {"query": "...", "source": "tscircuit"|"build123d"|null, "k": 5}
    POST /context   same, but returns a ready-to-paste prompt block within a char budget
    GET  /health

Held in memory: 2163 chunks is a few MB and BM25 over it takes under a millisecond, so
there is no store to run and nothing to keep warm. If the corpus grows by an order of
magnitude this is the thing to revisit — the retrieval interface would not change.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from docsrag.index import Retriever

INDEX = Path(os.environ.get(
    "DOCSRAG_INDEX",
    str(Path(__file__).resolve().parents[2] / "index" / "chunks.jsonl"),
))

app = FastAPI(title="docs-rag", version="1.0")
_retriever: Retriever | None = None


def retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever.from_jsonl(INDEX)
    return _retriever


class Query(BaseModel):
    query: str
    source: str | None = Field(
        default=None, description="tscircuit | build123d; omit to search both"
    )
    k: int = 5
    budget_chars: int = 6000


@app.get("/health")
def health() -> dict:
    r = retriever()
    by: dict[str, int] = {}
    for c in r.chunks:
        by[c["source"]] = by.get(c["source"], 0) + 1
    return {"ok": True, "chunks": r.n, "by_source": by, "index": str(INDEX)}


@app.post("/search")
def search(q: Query) -> dict:
    hits = retriever().search(q.query, k=q.k, source=q.source)
    return {"hits": [h.__dict__ for h in hits]}


@app.post("/context")
def context(q: Query) -> dict:
    """A prompt block, or an empty string when nothing matched.

    Empty rather than a low-confidence guess on purpose. Retrieval that always returns
    something teaches a model that the block is background noise; retrieval that returns
    nothing when it has nothing keeps the block meaning "this is the documentation".
    """
    text = retriever().context_for(
        q.query, k=q.k, source=q.source, budget_chars=q.budget_chars
    )
    return {"context": text, "chars": len(text)}
