"""Retrieval over the docs corpus.

BM25 with an exact-symbol boost, and no embedding model. That is a deliberate choice for
this corpus rather than a shortcut, and the reasoning is worth keeping:

The queries this serves are overwhelmingly *identifier* queries — "what props does
pinheader take", "how do I fillet an edge in build123d". The single failure this whole
component exists to prevent is a model writing `<connector>` with `pins="2"` when the
answer is `<pinheader pinCount={2}>`. A general-purpose sentence embedder is trained to
put `pins` and `pinCount` near each other; that is exactly the distinction that must not
blur. Lexical matching keeps them apart, and an exact symbol hit is a stronger signal
than any prose similarity.

It also costs no GPU. On a box where memory is the scarce resource and Isaac Sim, the CAD
service and two model servers already compete for it, a retriever that runs on CPU in
under a millisecond is worth more than a marginally better one that wants a slice.

Where this is genuinely weaker is conceptual paraphrase — "how do I make a rounded box"
against docs that only ever say "fillet". `Retriever` therefore takes an optional
`dense` callable, so a dense model can be layered in later without the callers changing.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# Split on non-alphanumerics but keep camelCase apart too, so `pinCount` also matches a
# query for `count`, and `maxDecouplingTraceLength` is reachable from `decoupling`.
_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on", "with",
    "how", "do", "i", "what", "does", "can", "you", "use", "using", "be", "this", "that",
}


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _SPLIT.split(text):
        if not raw:
            continue
        low = raw.lower()
        if low not in _STOP and len(low) > 1:
            out.append(low)
        # camelCase pieces, so identifiers are reachable by their parts as well as whole
        parts = _CAMEL.split(raw)
        if len(parts) > 1:
            out.extend(p.lower() for p in parts if len(p) > 1 and p.lower() not in _STOP)
    return out


@dataclass
class Hit:
    id: str
    source: str
    path: str
    title: str
    text: str
    score: float
    why: str


class Retriever:
    """BM25 over chunk text, plus a boost when a query term is a declared symbol."""

    K1 = 1.4
    B = 0.72
    SYMBOL_BOOST = 2.6
    TITLE_BOOST = 1.5
    # A filename match is near-definitive for API lookup: `docs/elements/capacitor.mdx`
    # *is* the capacitor documentation, whereas "capacitor" appears in passing across
    # dozens of tutorials. Without this, "capacitor element props" retrieved a guide on
    # React context that happened to use one in its example.
    PATH_BOOST = 3.2
    # Prop tables are what a generating model needs and prose is not. The pinheader page
    # splits into an intro chunk and a `| Property | Type |` chunk, and the intro was
    # winning — so the retrieved docs never contained the word `pinCount`, which is the
    # single token this whole component exists to put in front of the model.
    TABLE_BOOST = 1.9

    def __init__(self, chunks: list[dict], dense: Callable[[str], list[float]] | None = None):
        self.chunks = chunks
        self.dense = dense
        self.docs: list[list[str]] = []
        self.symbols: list[set[str]] = []
        self.titles: list[set[str]] = []
        self.stems: list[set[str]] = []
        self.has_table: list[bool] = []
        df: Counter[str] = Counter()
        for c in chunks:
            toks = tokenize(f"{c['title']} {c['text']}")
            self.docs.append(toks)
            self.symbols.append({s.lower() for s in c.get("symbols", [])})
            self.titles.append(set(tokenize(c["title"])))
            self.stems.append(set(tokenize(Path(c["path"]).stem)))
            self.has_table.append("| Property" in c["text"] or "| Prop " in c["text"]
                                  or "| Type |" in c["text"])
            df.update(set(toks))
        self.n = len(chunks)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 0.0
        self.idf = {
            t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }
        self.tf: list[Counter[str]] = [Counter(d) for d in self.docs]

    @classmethod
    def from_jsonl(cls, path: str | Path, **kw) -> "Retriever":
        chunks = [json.loads(l) for l in Path(path).read_text(encoding="utf8").splitlines() if l.strip()]
        return cls(chunks, **kw)

    def search(self, query: str, k: int = 5, source: str | None = None) -> list[Hit]:
        qtoks = tokenize(query)
        if not qtoks:
            return []
        qset = set(qtoks)
        scored: list[tuple[float, int, str]] = []
        for i, c in enumerate(self.chunks):
            if source and c["source"] != source:
                continue
            tf, dl = self.tf[i], len(self.docs[i])
            score = 0.0
            for t in qtoks:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                score += idf * (f * (self.K1 + 1)) / (
                    f + self.K1 * (1 - self.B + self.B * dl / (self.avgdl or 1))
                )
            if score <= 0:
                continue
            reasons = []
            sym_hits = qset & self.symbols[i]
            if sym_hits:
                score *= self.SYMBOL_BOOST
                reasons.append("symbol:" + ",".join(sorted(sym_hits)[:3]))
            title_hits = qset & self.titles[i]
            if title_hits:
                score *= self.TITLE_BOOST
                reasons.append("title:" + ",".join(sorted(title_hits)[:3]))
            stem_hits = qset & self.stems[i]
            if stem_hits:
                score *= self.PATH_BOOST
                reasons.append("path:" + ",".join(sorted(stem_hits)[:2]))
            if self.has_table[i] and ("props" in qset or "prop" in qset
                                      or "properties" in qset):
                score *= self.TABLE_BOOST
                reasons.append("proptable")
            scored.append((score, i, "; ".join(reasons) or "text"))
        scored.sort(key=lambda t: -t[0])
        out: list[Hit] = []
        for score, i, why in scored[:k]:
            c = self.chunks[i]
            out.append(Hit(id=c["id"], source=c["source"], path=c["path"],
                           title=c["title"], text=c["text"], score=round(score, 3), why=why))
        return out

    def context_for(self, query: str, k: int = 4, source: str | None = None,
                    budget_chars: int = 6000) -> str:
        """Retrieved chunks as a prompt block, truncated to a budget.

        The budget is a hard limit rather than advice. Context is the scarcest thing in
        this pipeline — plan §8.8 measured a designer turn at 14.3k tokens against a 32k
        window — so retrieval that quietly doubles the prompt trades one failure for
        another.
        """
        hits = self.search(query, k=k, source=source)
        if not hits:
            return ""
        parts, used = [], 0
        for h in hits:
            head = f"--- {h.source}: {h.path} — {h.title} ---\n"
            room = budget_chars - used - len(head)
            if room < 200:
                break
            body = h.text
            if len(body) > room:
                # Truncate rather than drop. The first version broke out of the loop when
                # a block did not fit, which meant a top hit larger than the budget
                # returned *nothing at all* — the better the match, the more likely it was
                # to be long enough to be discarded. A signature cut short still answers
                # the question; an empty block answers nothing and looks like "no docs
                # exist for this", which is a different and worse claim.
                body = body[:room].rstrip() + "\n… (truncated)"
            parts.append(head + body)
            used += len(head) + len(body)
        if not parts:
            return ""
        return (
            "<docs-retrieved>\n"
            "Authoritative extracts from the real documentation. Where these disagree "
            "with your recollection of the API, these are right.\n\n"
            + "\n\n".join(parts)
            + "\n</docs-retrieved>"
        )
