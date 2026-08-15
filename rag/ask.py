#!/usr/bin/env python
"""Ask the docs corpus a question from the terminal.

    ./ask.py fillet an edge with a radius
    ./ask.py --source tscircuit pinheader props
    ./ask.py --full extrude a sketch

This is the build123d corpus's real consumer, and saying so is more useful than
pretending otherwise. The CAD design loop does *not* write build123d code: it emits a
RobotIR naming hand-written geometry generators (`tube`, `plate`, `bracket`, …) and
passing them params, so its prompt already contains everything it needs and retrieval
would be noise in it. What build123d docs are actually for here is **authoring the next
generator** — a development task, done at a terminal, against an API with a large surface
and docstring-only reference.

The tscircuit half is the opposite: that agent writes the HDL directly, so retrieval goes
into its prompt (`pcb-ai/src/docs-rag.ts`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from docsrag.index import Retriever  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--source", choices=["tscircuit", "build123d"], default=None)
    ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--full", action="store_true", help="print whole chunks, not extracts")
    a = ap.parse_args()

    index = Path(__file__).resolve().parent / "index" / "chunks.jsonl"
    if not index.exists():
        print("no index — run ./refresh.sh first", file=sys.stderr)
        return 2

    hits = Retriever.from_jsonl(index).search(" ".join(a.query), k=a.k, source=a.source)
    if not hits:
        print("nothing matched")
        return 1
    for h in hits:
        print(f"\n\033[1m{h.source}: {h.path} — {h.title}\033[0m  ({h.score}, {h.why})")
        print(h.text if a.full else h.text[:700] + ("…" if len(h.text) > 700 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
