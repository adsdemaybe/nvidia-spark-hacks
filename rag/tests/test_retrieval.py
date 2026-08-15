"""Retrieval quality, pinned to the failures that motivated it.

Every assertion here traces to something a model actually got wrong while generating a
board, so a regression in ranking shows up as a named failure rather than as a board that
is slightly worse for reasons nobody can attribute.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docsrag.index import Retriever, tokenize  # noqa: E402
from docsrag.ingest import symbols_in  # noqa: E402

INDEX = ROOT / "index" / "chunks.jsonl"
pytestmark = pytest.mark.skipif(not INDEX.exists(), reason="corpus not ingested")


@pytest.fixture(scope="module")
def r() -> Retriever:
    return Retriever.from_jsonl(INDEX)


def top_paths(hits) -> list[str]:
    return [h.path for h in hits]


# --- the failures this exists to prevent -----------------------------------------


def test_pinheader_query_reaches_the_pinheader_page(r: Retriever):
    """The model wrote `<pinheader pins="2">`. The answer is `pinCount={2}`."""
    hits = r.search("pinheader element props and example", k=3, source="tscircuit")
    assert any("pinheader" in p for p in top_paths(hits)), top_paths(hits)


def test_the_prop_table_is_retrievable_not_just_the_intro(r: Retriever):
    """An element page splits into prose and a `| Property | Type |` table.

    The intro wins on score alone, and the intro is the half that does not contain the
    prop names — so retrieval that stops at k=1 supplies everything except the answer.
    """
    hits = r.search("pinheader element props and example", k=4, source="tscircuit")
    assert any("pinCount" in h.text for h in hits), [h.title for h in hits]


def test_connectivity_docs_are_reachable(r: Retriever):
    """The model invented `<connection pin=... net=...>` as a child element.

    Connections are `<trace>` siblings. Nothing in the corpus should rank above trace.mdx
    for a question about connecting pins.
    """
    hits = r.search("connect pins with a trace", k=3, source="tscircuit")
    assert "docs/elements/trace.mdx" in top_paths(hits), top_paths(hits)


def test_a_filename_match_outranks_incidental_mentions(r: Retriever):
    """`capacitor element props` used to return a React-context guide.

    `docs/elements/capacitor.mdx` *is* the capacitor documentation; every tutorial that
    happens to place one is not.
    """
    hits = r.search("capacitor element props and example", k=2, source="tscircuit")
    assert any("elements/capacitor" in p for p in top_paths(hits)), top_paths(hits)


# --- build123d, whose API lives in docstrings ------------------------------------


@pytest.mark.parametrize(
    "query,expect",
    [
        ("fillet an edge with a radius", "fillet"),
        ("create a Box solid with length width height", "Box"),
        ("extrude a sketch into a part", "extrude"),
        ("revolve a profile around an axis", "revolve"),
    ],
)
def test_build123d_api_lookup(r: Retriever, query: str, expect: str):
    hits = r.search(query, k=3, source="build123d")
    assert any(expect.lower() in h.title.lower() for h in hits), [h.title for h in hits]


def test_build123d_signatures_are_present_not_only_prose(r: Retriever):
    """The docstring says what a Box is for; the signature says what it takes.

    Both matter, and the ast-based ingest exists because the regex one captured only the
    first class per file — 11 classes in objects_part.py yielded 1, silently.
    """
    hits = r.search("create a Box solid with length width height", k=3, source="build123d")
    box = next((h for h in hits if h.title == "class Box"), None)
    assert box is not None, [h.title for h in hits]
    assert "length" in box.text and "width" in box.text


# --- mechanics -------------------------------------------------------------------


def test_source_filter_is_honoured(r: Retriever):
    for src in ("tscircuit", "build123d"):
        assert all(h.source == src for h in r.search("example", k=8, source=src))


def test_camel_case_is_reachable_by_its_parts():
    """`pinCount` must be findable from `count`, or a plain-English query never lands."""
    assert "count" in tokenize("pinCount")
    assert "pincount" in tokenize("pinCount")


def test_symbols_capture_jsx_and_python_identifiers():
    syms = symbols_in('<pinheader pinCount={2} />\nclass Box:\n    """A box."""')
    assert "pinheader" in syms
    assert "Box" in syms


def test_an_empty_or_stopword_query_returns_nothing_rather_than_everything(r: Retriever):
    assert r.search("", k=5) == []
    assert r.search("the a of to", k=5) == []


def test_context_budget_is_a_hard_limit(r: Retriever):
    """Context is the scarcest resource in the pipeline; retrieval must not double a prompt."""
    for budget in (600, 1500, 4000):
        text = r.context_for("pinheader props", k=6, source="tscircuit", budget_chars=budget)
        assert len(text) <= budget + 400, (budget, len(text))  # + wrapper


def test_a_hit_larger_than_the_budget_is_truncated_not_dropped():
    """The first version returned "" when the top hit did not fit.

    The better the match, the longer it tended to be, so the best answers were the ones
    most likely to be discarded — and an empty block reads as "there are no docs for
    this", which is a different and worse claim than a truncated one.

    Built on a synthetic chunk rather than the real corpus on purpose: whether any given
    page happens to exceed a given budget is a fact about the docs that day, and a test
    that depends on it passes or fails for reasons unrelated to the code. This one is
    about the branch.
    """
    oversized = {
        "id": "t:big", "source": "tscircuit", "path": "docs/elements/pinheader.mdx",
        "title": "pinheader", "text": "pinCount " + ("filler words here. " * 400),
        "symbols": ["pinheader", "pinCount"],
    }
    small = Retriever([oversized])
    text = small.context_for("pinheader", k=1, budget_chars=700)
    assert text, "a too-large top hit must still produce context, not an empty string"
    assert "truncated" in text
    assert len(text) <= 700 + 400
