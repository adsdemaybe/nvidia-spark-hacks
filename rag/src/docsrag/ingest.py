"""Turn two very different documentation sources into one chunk format.

tscircuit ships `llms.txt` — a Repomix dump of its docs repo, every file wrapped in a
`<file path="...">` tag. build123d ships reStructuredText plus the Python source its API
reference is generated from, so the docstrings are part of the corpus, not a separate
thing.

The chunk is deliberately small and carries `symbols`: the identifiers found in it —
element names, class names, prop names. Retrieval for this job is mostly *identifier
lookup* ("what props does pinheader take?"), and an exact symbol hit is a far stronger
signal than any amount of prose similarity. Keeping them as a separate field means the
ranker can say so rather than hoping the tokeniser preserved them.

Chunks are split on headings rather than on a fixed token count. An API signature and the
prose explaining it belong together; a window that lands between them retrieves half an
answer, which is worse than retrieving nothing because it looks complete.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

MAX_CHARS = 2400
MIN_CHARS = 40


@dataclass
class Chunk:
    id: str
    source: str
    path: str
    title: str
    text: str
    symbols: list[str]

    def to_json(self) -> dict:
        return asdict(self)


# JSX/HTML element names, `code spans`, python defs/classes, and prop= names. These are
# what a question is actually about when it is about an API.
_JSX = re.compile(r"<([a-z][a-zA-Z0-9_]*)")
_CODE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{2,})`")
_PYDEF = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_PROP = re.compile(r"\b([a-z][a-zA-Z0-9]{2,})\s*=")
_RST_DIR = re.compile(r"^\.\.\s+auto(?:class|function|method)::\s*([A-Za-z_][\w.]*)", re.M)


def symbols_in(text: str) -> list[str]:
    found: set[str] = set()
    for pat in (_JSX, _CODE, _PYDEF, _PROP, _RST_DIR):
        for m in pat.findall(text):
            if 2 < len(m) < 48:
                found.add(m)
    return sorted(found)


def _emit(out: list[Chunk], source: str, path: str, title: str, body: str) -> None:
    body = body.strip()
    if len(body) < MIN_CHARS:
        return
    # Only split when a section genuinely exceeds the budget, and split on blank lines so
    # a fenced example is not cut in half.
    parts = [body]
    if len(body) > MAX_CHARS:
        parts, cur = [], ""
        for para in body.split("\n\n"):
            if len(cur) + len(para) > MAX_CHARS and cur:
                parts.append(cur)
                cur = para
            else:
                cur = f"{cur}\n\n{para}" if cur else para
        if cur:
            parts.append(cur)
    for i, part in enumerate(parts):
        suffix = f"#{i}" if len(parts) > 1 else ""
        out.append(Chunk(
            id=f"{source}:{path}{suffix}",
            source=source, path=path,
            title=title if not suffix else f"{title} (cont. {i + 1})",
            text=part, symbols=symbols_in(part),
        ))


_MD_HEAD = re.compile(r"^(#{1,4})\s+(.*)$", re.M)


def _split_markdown(source: str, path: str, text: str, out: list[Chunk]) -> None:
    heads = list(_MD_HEAD.finditer(text))
    if not heads:
        _emit(out, source, path, Path(path).stem, text)
        return
    if heads[0].start() > 0:
        _emit(out, source, path, Path(path).stem, text[: heads[0].start()])
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        _emit(out, source, path, h.group(2).strip(), text[h.start():end])


def ingest_tscircuit(llms_txt: Path) -> list[Chunk]:
    """Repomix wraps each file as `<file path="...">…</file>`."""
    raw = llms_txt.read_text(encoding="utf8", errors="replace")
    out: list[Chunk] = []
    for m in re.finditer(r'<file path="([^"]+)">\n(.*?)\n</file>', raw, re.S):
        path, body = m.group(1), m.group(2)
        if not path.startswith("docs/"):
            continue
        _split_markdown("tscircuit", path, body, out)
    return out


_RST_HEAD = re.compile(r"^(.+)\n([=\-~^\"'`#*+]{3,})\s*$", re.M)


def ingest_build123d_docs(docs_dir: Path) -> list[Chunk]:
    out: list[Chunk] = []
    for f in sorted(docs_dir.glob("*.rst")):
        text = f.read_text(encoding="utf8", errors="replace")
        heads = list(_RST_HEAD.finditer(text))
        rel = f"docs/{f.name}"
        if not heads:
            _emit(out, "build123d", rel, f.stem, text)
            continue
        if heads[0].start() > 0:
            _emit(out, "build123d", rel, f.stem, text[: heads[0].start()])
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            _emit(out, "build123d", rel, h.group(1).strip(), text[h.start():end])
    return out


def ingest_build123d_source(src_dir: Path) -> list[Chunk]:
    """Top-level classes and functions, with their signatures and docstrings.

    build123d's API reference is generated from these, so a corpus without them can say
    what a Box is for and not what arguments it takes — which is the half that stops a
    model inventing them.

    Parsed with `ast`, not regex. The regex version silently captured only the *first*
    class in each file: a whitespace class matches newlines, so the "indented body" alternation ran
    straight through every following top-level definition. objects_part.py has 11 classes
    and yielded 1, and nothing about the output looked wrong — the chunks it did produce
    were perfectly good. Python parses its own syntax correctly and cost less code.
    """
    import ast

    out: list[Chunk] = []
    for f in sorted(src_dir.rglob("*.py")):
        if f.name.startswith("_") and f.name != "__init__.py":
            continue
        text = f.read_text(encoding="utf8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = f"src/{f.relative_to(src_dir)}"
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                continue
            doc = ast.get_docstring(node) or ""
            if not doc:
                continue  # undocumented internals are noise, not corpus
            kind = "class" if isinstance(node, ast.ClassDef) else "def"

            # The signature is what a caller needs and the docstring usually omits.
            if isinstance(node, ast.ClassDef):
                init = next((n for n in node.body
                             if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
                sig = f"{node.name}{_sig(init)}" if init else node.name
            else:
                sig = f"{node.name}{_sig(node)}"

            body = f"{kind} {sig}\n\n{doc[:MAX_CHARS]}"
            _emit(out, "build123d", rel, f"{kind} {node.name}", body)
    return out


def _sig(fn) -> str:
    """A readable call signature, annotations kept, `self` dropped."""
    import ast

    args = []
    a = fn.args
    positional = a.posonlyargs + a.args
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    for arg, default in zip(positional, defaults):
        if arg.arg == "self":
            continue
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {ast.unparse(default)}"
        args.append(piece)
    if a.vararg:
        args.append(f"*{a.vararg.arg}")
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {ast.unparse(default)}"
        args.append(piece)
    if a.kwarg:
        args.append(f"**{a.kwarg.arg}")
    return "(" + ", ".join(args) + ")"


def build_corpus(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    ts = root / "tscircuit-llms.txt"
    if ts.exists():
        chunks += ingest_tscircuit(ts)
    b = root / "b123d"
    if (b / "docs").exists():
        chunks += ingest_build123d_docs(b / "docs")
    if (b / "src" / "build123d").exists():
        chunks += ingest_build123d_source(b / "src" / "build123d")
    return chunks


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(Path(__file__).resolve().parents[2] / "corpus"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "index" / "chunks.jsonl"))
    a = ap.parse_args()

    chunks = build_corpus(Path(a.corpus))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf8") as fh:
        for c in chunks:
            fh.write(json.dumps(c.to_json()) + "\n")

    by_source: dict[str, int] = {}
    for c in chunks:
        by_source[c.source] = by_source.get(c.source, 0) + 1
    print(f"{len(chunks)} chunks -> {out}")
    for s, n in sorted(by_source.items()):
        print(f"  {s:<12} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
