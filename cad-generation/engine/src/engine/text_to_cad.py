"""Text to build123d, with the harness deciding whether it worked.

The loop is the same one the rest of the platform runs on. The model proposes code; it
never says whether the part is good. `build()` executes it, OpenCascade measures it, and
if the code raises, the error text goes back as the work order. The model gets the real
traceback, because "it failed" is not something anyone can act on and
`Standard_ConstructionError` is.

Grounding comes from the docs retriever (`rag/`, port 8220). This is where the build123d
corpus earns its place: the *design* agent picks named generators and needs no API docs,
but an agent writing build123d directly needs `fillet`'s signature in front of it, and
build123d's reference lives in docstrings that a model half-remembers.

    from engine.text_to_cad import text_to_part
    result = text_to_part("an L-bracket 60mm x 40mm with two M5 clearance holes", model)
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from build123d import GeomType

from engine.geometry.freeform import FreeformError
from engine.geometry.inspect import profile
from engine.geometry.registry import build
from engine.ir import CatalogueParam, GeometrySpec

DOCS_RAG_URL = "http://127.0.0.1:8220"

SYSTEM = """\
You write build123d code that produces one solid part, which will be exported to STL
and printed. Every rule below exists because it has already gone wrong here.

OUTPUT
- Assign the finished solid to a variable named `part`. Nothing else is read.
- build123d is already imported (`from build123d import *`) and so is `math`. Do not
  write any import statement; there is no file, network or process access.
- Reply with exactly one ```python code block and nothing else. No prose around it.

WHAT COUNTS AS A PART
- Produce a SOLID, not a sketch. A `Circle` is not a part; extrude or revolve it.
- Units are millimetres throughout. build123d's native unit is mm and the harness
  converts at its own edge — do not scale anything yourself.
- One connected solid. Two boxes that touch at a corner are two solids to a mesher,
  and the STL exports as a shell a slicer will refuse. If the design has separate
  bodies, fuse them along a real overlapping face.
- No zero-thickness or self-intersecting geometry. Both export as valid-looking STL
  and fail at the slicer, which is the most expensive place to find out.

BUILDER SCOPE — the single most common failure
- Every operation that modifies the part — `Hole`, `fillet`, `chamfer`, `extrude` —
  belongs INSIDE the `with BuildPart()` block that owns it. A `Hole()` written after
  the block closes cuts nothing, leaves the solid undrilled, and raises
  `ValueError: No depth provided` — an error about the missing builder, not about the
  depth. Adding a `depth=` will not fix it; moving the call inside the block will.

FEATURES MUST LAND ON MATERIAL
- A cut positioned outside the solid is a silent no-op: OpenCascade removes nothing,
  returns a valid part, and every bulk measurement passes. Three separate design runs
  here shipped a part whose mounting holes were outside the face they were meant to go
  through. Before placing a hole, check its centre lies inside the face it drills, with
  clearance for its own radius.
- A blind bore and a through bore measure identically in volume and bounding box. If
  the request says "through", make the cut longer than the material it passes through.

REAL DIMENSIONS
- If the request implies a fastener, use its clearance size: M2 2.4mm, M2.5 2.9mm,
  M3 3.4mm, M4 4.5mm, M5 5.5mm. A hole drilled at nominal thread diameter does not
  pass the screw.
- Do not invent a dimension the request did not give and does not imply. If something
  is genuinely unspecified, choose the conventional value and it will be measured
  against the request — but never silently resize something that WAS specified to make
  the geometry work.

PRINTABILITY — this is going to a printer, not a render
- Minimum wall 1.2mm, minimum free-standing feature 2mm. Thinner survives CAD and
  does not survive FDM.
- Unsupported overhangs beyond 45 degrees from vertical need a chamfer or a fillet,
  not a prayer. A horizontal hole through a vertical wall prints as a droop unless it
  is teardropped or oriented along Z.
- Put the largest flat face on the XY plane at Z=0. The harness does not reorient the
  part, so the orientation you build is the orientation it prints in."""


def _cylinder_radius(face) -> float | None:
    """`Face.radius`, but None instead of an exception on a face that reports
    `GeomType.CYLINDER` while carrying a trimmed surface underneath."""
    try:
        r = face.radius
    except Exception:  # noqa: BLE001 — any OCC surface we cannot read is "not a bore"
        return None
    return float(r) if r is not None else None


def _cylinder_axis(face):
    """The face's cylinder axis, or None if OCC will not give one.

    A subtracted cylinder can leave a face whose `geom_type` is CYLINDER but whose
    underlying surface is a `Geom_RectangularTrimmedSurface`, which has no
    `.Cylinder()`. Those are the *same* bore as the untrimmed half beside them, so
    dropping them costs no count and asking them for an axis raises.
    """
    try:
        return face.geom_adaptor().Cylinder().Axis()
    except Exception:  # noqa: BLE001
        return None


def _bore_axes(faces, tol: int = 3) -> set:
    """Distinct cylinder axes among `faces`, so co-axial faces count once.

    The axis is reduced to (direction, the point on it closest to the origin) so
    that two half-cylinders of the same hole — same line, different `Location()`
    along it — collapse to one entry. Directions are normalised for sign because
    OpenCascade is free to orient either half's axis the other way.
    """
    axes = set()
    for f in faces:
        ax = _cylinder_axis(f)
        if ax is None:
            continue
        d = ax.Direction()
        p = ax.Location()
        dv = (d.X(), d.Y(), d.Z())
        if next((c for c in dv if abs(c) > 1e-9), 1.0) < 0:  # first non-zero decides sign
            dv = (-dv[0], -dv[1], -dv[2])
        pv = (p.X(), p.Y(), p.Z())
        slide = sum(a * b for a, b in zip(pv, dv))
        foot = tuple(round(a - slide * b, tol) for a, b in zip(pv, dv))
        axes.add((tuple(round(c, tol) for c in dv), foot,
                  round(_cylinder_radius(f) or 0.0, tol)))
    return axes


# Every key `check_expectations` understands. Anything else raises — see there.
_EXPECT_KEYS = frozenset(
    {"through_bore", "bbox_mm", "bbox_tol_mm", "bores", "volume_mm3", "volume_tol_frac"}
)


def check_expectations(part, expect: dict) -> list[str]:
    """Machine-checkable claims about the finished solid, verified against it.

    This exists because a part can pass every bulk measurement and still be wrong. Asked
    for a 12mm spacer with a 5.2mm bore, the model produced one whose bore stops halfway:
    it built, exported, imported, and measured at 819.8 mm³ / 1.02 g — all correct **for
    the solid that was built** — with a bounding box of exactly 12 x 12 x 8. Nothing the
    harness checked was wrong, and the part was not the part.

    So the claims have to be about features, not totals. `through_bore` is the one that
    catches that failure, and it catches it by asking a question bulk properties cannot
    express: does the hole reach the far face.
    """
    # An unknown key is a caller error, and it has to be loud. Silently ignoring
    # one means the check never runs and the part passes unverified, which is the
    # exact failure this function exists to prevent — dressed up as a pass. It
    # happened while writing this: `--expect '{"through_bores": 7}'` (plural) on a
    # bracket was accepted, checked nothing, and reported a gate that had not run.
    #
    # Raised rather than returned as a `problem`, because a problem triggers a
    # retry and no amount of regenerating the part fixes a typo in the caller's
    # expectation.
    unknown = set(expect) - _EXPECT_KEYS
    if unknown:
        raise ValueError(
            f"unknown expectation key(s): {sorted(unknown)}. Known keys: "
            f"{sorted(_EXPECT_KEYS)}. An unrecognised key would check nothing and "
            "report a pass."
        )

    problems: list[str] = []

    axis = expect.get("through_bore")
    if axis:
        pr = profile(part, str(axis))
        if not pr.bore_is_through:
            problems.append(
                f"the bore does not go through. {pr.describe()}. A section just inside "
                f"one end shows no hole, so it is a blind pocket. This is usually mixed "
                f"alignment: a default-aligned outer solid is centred on the origin while "
                f"an Align.MIN cutter starts at it, so they only overlap for half the "
                f"length. Give both the same alignment, or make the cutter longer than "
                f"the solid."
            )

    bbox = expect.get("bbox_mm")
    if bbox:
        bb = part.bounding_box()
        got = (bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)
        tol = float(expect.get("bbox_tol_mm", 0.5))
        if any(abs(g - w) > tol for g, w in zip(got, bbox)):
            problems.append(
                f"bounding box is {tuple(round(v, 2) for v in got)} mm, expected "
                f"{tuple(bbox)} mm (±{tol})"
            )

    bores = expect.get("bores")
    if bores:
        # Asked for an L-bracket "with two M5 clearance holes", the model wrote the
        # holes into a `with Locations(...)` block placed *after* the BuildPart
        # context had closed, so they were cut into nothing and `bracket.part` was
        # the undrilled solid. It built, measured 19000.0 mm³, and had a bounding
        # box of exactly 60 x 40 x 40 — the box the request implies with or without
        # holes. `through_bore` could not see it either: there was no bore to
        # profile. Counting the bores is the check that fails.
        want_d = float(bores["diameter_mm"])
        want_n = int(bores.get("count", 1))
        tol = float(bores.get("diameter_tol_mm", 0.2))
        matching = []
        for f in part.faces():
            if f.geom_type != GeomType.CYLINDER:
                continue
            r = _cylinder_radius(f)
            if r is not None and abs(r * 2 - want_d) <= tol:
                matching.append(f)
        # Count axes, not faces. A through-hole is frequently split into two
        # half-cylinder faces, and counting faces let a bracket through with two
        # of them — which was one hole drilled at x=15 and a second at x=45 that
        # missed the 60mm leg entirely and cut nothing. Two faces, one hole.
        found = len(_bore_axes(matching))
        if found < want_n:
            diameters = sorted({round(r * 2, 2) for r in
                                (_cylinder_radius(f) for f in part.faces()
                                 if f.geom_type == GeomType.CYLINDER)
                                if r is not None})
            problems.append(
                f"expected {want_n} bore(s) of {want_d}mm diameter, found {found}. "
                f"Cylindrical faces present: {diameters or 'none'}. A `Hole()` outside "
                f"the `with BuildPart()` block cuts nothing and leaves the solid "
                f"undrilled; put the hole operations inside the same context that owns "
                f"the part, or subtract an explicit Cylinder in algebra mode."
            )

    vol = expect.get("volume_mm3")
    if vol:
        got = part.volume
        tol = float(expect.get("volume_tol_frac", 0.05))
        if abs(got - vol) / vol > tol:
            problems.append(
                f"volume is {got:.1f} mm³, expected {vol:.1f} mm³ "
                f"(off by {abs(got - vol) / vol * 100:.1f}%, tolerance {tol * 100:.0f}%)"
            )

    return problems


@dataclass
class TextToPartResult:
    ok: bool
    code: str
    attempts: int
    mass_kg: float | None = None
    volume_m3: float | None = None
    bbox_m: tuple[float, float, float] | None = None
    errors: list[str] = field(default_factory=list)
    # {"stl": "...", "step": "..."} — written only when `out_stem` was given.
    # Empty is the normal case: `text_to_part` is a pure measurement by default,
    # and a function that writes files whether or not you asked is a function
    # you cannot call inside a search loop.
    artifacts: dict[str, str] = field(default_factory=dict)
    # Which model wrote the code. Recorded because §7 scores prediction accuracy
    # per agent, and "a human pasted this in" is a different provenance from
    # "the local model generated it on attempt 3".
    author: str = ""


def _reexportable(part):
    """Re-wrap a solid that came back from `import_step` so it can be written out.

    `export_step` raises a bare `RuntimeError: Failed to write STEP file` for a
    shape produced by `import_step`, while writing an identically-shaped freshly
    constructed one without complaint. The underlying OCCT `TopoDS_Shape` is
    fine — re-wrapping it in a new build123d object exports cleanly — so this is
    Python-side wrapper state, not bad geometry.

    It matters here specifically because *every* freeform part is an imported
    STEP: `run_to_step` builds in a subprocess and returns a file, so the object
    this module measures has always been through `import_step` and could never
    be exported. STL was unaffected and wrote fine, which is what made the
    failure look like a STEP-only quirk rather than "the freeform path cannot
    export at all".
    """
    if getattr(part, "wrapped", None) is None:
        return part
    try:
        return type(part)(part.wrapped)
    except Exception:
        # A shape class whose constructor does not take a raw wrapped shape.
        # Exporting the original is still the right attempt — better a real
        # error from the exporter than a silent substitution.
        return part


def export_part(part, out_stem, *, formats: tuple[str, ...] = ("stl", "step")) -> dict[str, str]:
    """Write the measured solid to disk. The step `text_to_part` used to skip.

    Until now the pipeline built a genuine B-rep, measured it, reported its mass
    and bounding box — and dropped it. Everything downstream was a number about a
    part nobody could open, print or look at.

    STL is a mesh and therefore lossy, so STEP is written alongside by default:
    the STL is what a slicer takes, the STEP is what survives being edited. The
    linear tolerance is 0.1 mm rather than build123d's default — on a 12 mm bore
    the default's faceting is visible as flats, and a printed part measured
    across them is undersized in a way that reads as a design error.
    """
    from pathlib import Path

    out_stem = Path(out_stem)
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    part = _reexportable(part)
    written: dict[str, str] = {}
    if "stl" in formats:
        from build123d import export_stl

        path = out_stem.with_suffix(".stl")
        export_stl(part, str(path), tolerance=0.1, angular_tolerance=0.2)
        written["stl"] = str(path)
    if "step" in formats:
        from build123d import export_step

        path = out_stem.with_suffix(".step")
        export_step(part, str(path))
        written["step"] = str(path)
    return written


def retrieve_docs(query: str, k: int = 3, budget_chars: int = 4000,
                  url: str = DOCS_RAG_URL) -> str:
    """Best-effort. A docs service being down must not stop a part being made."""
    try:
        req = urllib.request.Request(
            f"{url}/context",
            data=json.dumps({"query": query, "source": "build123d", "k": k,
                             "budget_chars": budget_chars}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return json.load(resp).get("context", "")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""


def extract_code(reply: str) -> str:
    """The fenced block, or the whole reply if the model forgot the fence."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip()


def text_to_part(
    description: str,
    ask,
    *,
    material: str = "pla",
    max_attempts: int = 3,
    docs: bool = True,
    expect: dict | None = None,
    out_stem=None,
    author: str = "",
) -> TextToPartResult:
    """Describe a part in words; get a measured solid or an honest failure.

    `ask(system, user) -> str` is the whole model interface, so this is testable with a
    stub and works against any backend. Passing an `ask` that ignores its arguments and
    returns fixed code is the supported way to build **author-written** code through the
    same gates — see `code_to_part`.

    `max_attempts` is a budget, not a guarantee. Each retry carries the previous code and
    the error it raised — which is the only thing that makes retrying worth anything.

    `out_stem` writes `<stem>.stl` and `<stem>.step` on success. Omitted by default: a
    function that writes files whether or not you asked cannot be called inside a search
    loop.
    """
    mat = CatalogueParam(kind="catalogue", value=material, catalogue="materials")
    context = retrieve_docs(description) if docs else ""
    errors: list[str] = []
    code = ""

    for attempt in range(1, max_attempts + 1):
        if attempt == 1:
            user = f"{context}\n\nMake this part:\n{description}" if context else \
                   f"Make this part:\n{description}"
        else:
            # The failure verbatim. A paraphrase loses the identifier that names the fix.
            user = (
                f"{context}\n\nThis code was supposed to make: {description}\n\n"
                f"```python\n{code}\n```\n\n"
                f"It failed with:\n{errors[-1]}\n\n"
                f"Return the corrected code."
            )
        code = extract_code(ask(SYSTEM, user))

        try:
            result = build(GeometrySpec(generator="freeform",
                                        params={"code": code}, material=mat))
        except FreeformError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 — anything OCC raises is a failed part
            errors.append(f"{type(exc).__name__}: {exc}")
            continue

        # A solid that built is not a solid that is right. Feature checks run here, and a
        # failure is a retry with the reason — the same contract as a build error.
        if expect:
            problems = check_expectations(result.part, expect)
            if problems:
                errors.append("; ".join(problems))
                continue

        mp = result.mass_properties
        artifacts = export_part(result.part, out_stem) if out_stem else {}
        return TextToPartResult(
            ok=True, code=code, attempts=attempt,
            mass_kg=mp.mass, volume_m3=mp.volume,
            bbox_m=(mp.bbox_size.x, mp.bbox_size.y, mp.bbox_size.z),
            errors=errors, artifacts=artifacts, author=author,
        )

    # No artifacts on failure, deliberately. A written STL is a claim that a part
    # exists; the last attempt did not produce one, and half a part on disk is
    # the thing somebody sends to a printer at 2am.
    return TextToPartResult(
        ok=False, code=code, attempts=max_attempts, errors=errors, author=author
    )


def code_to_part(
    code: str,
    *,
    description: str = "author-supplied build123d",
    material: str = "pla",
    expect: dict | None = None,
    out_stem=None,
    author: str = "author",
) -> TextToPartResult:
    """Build code somebody already wrote, through the identical gates.

    This is the path for code written **in a chat session** rather than generated
    by the served model — and the point is that it gets no privileges. Same
    `freeform` subprocess, same OpenCascade measurement, same `expect` checks,
    same refusal to write an STL for something that did not build. The author
    proposes; the harness still disposes.

    One attempt, not three: a retry loop exists to feed an error back to whoever
    can act on it, and here that is a human reading the traceback, not a call
    this function can make.
    """
    return text_to_part(
        description,
        lambda system, user: f"```python\n{code}\n```",
        material=material,
        max_attempts=1,
        docs=False,
        expect=expect,
        out_stem=out_stem,
        author=author,
    )


def local_ask(base_url: str, model: str, timeout_s: float = 900.0):
    """An `ask` bound to the local OpenAI-compatible server. No hosted API.

    Same transport rule as `engine.agent_loop`: the `openai` package is an HTTP
    client for vLLM on :8100 and nothing leaves the box. Plain text out rather
    than a constrained JSON schema, because the reply here is a fenced code
    block — `extract_code` is the parser, and a schema around a string buys
    nothing.
    """
    import os

    import openai

    client = openai.OpenAI(
        base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "not-needed")
    )

    def ask(system: str, user: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4000,
            timeout=timeout_s,
        )
        return response.choices[0].message.content or ""

    return ask


def main(argv: list[str] | None = None) -> int:
    """`python -m engine.text_to_cad "<description>" --out part`

    Two ways in, one gate. `--code` builds source written by a human or by an
    assistant in a chat session; without it the served local model writes the
    code from the description. Neither is trusted: both go through the same
    `freeform` subprocess, the same OpenCascade measurement and the same
    `--expect` checks, and neither gets an STL written unless it built.
    """
    import argparse
    import json as _json
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m engine.text_to_cad")
    parser.add_argument("description", help="what the part is, in words")
    parser.add_argument("--out", default="", help="output stem; writes <stem>.stl and <stem>.step")
    parser.add_argument("--code", default="", help="path to build123d source to build instead of generating it")
    parser.add_argument("--material", default="pla", help="catalogue key; sets density, so mass")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--model", default="qwen3-coder-next")
    parser.add_argument("--base-url", default="http://localhost:8100/v1")
    parser.add_argument("--no-docs", action="store_true", help="skip the build123d docs RAG")
    parser.add_argument("--expect", default="", help='JSON, e.g. \'{"through_bores": 2}\'')
    args = parser.parse_args(argv)

    expect = _json.loads(args.expect) if args.expect else None
    stem = args.out or None

    if args.code:
        source = Path(args.code).read_text(encoding="utf-8")
        result = code_to_part(
            source, description=args.description, material=args.material,
            expect=expect, out_stem=stem, author=f"author:{args.code}",
        )
    else:
        result = text_to_part(
            args.description,
            local_ask(args.base_url, args.model),
            material=args.material, max_attempts=args.attempts,
            docs=not args.no_docs, expect=expect, out_stem=stem,
            author=f"model:{args.model}",
        )

    if not result.ok:
        print(f"FAILED after {result.attempts} attempt(s) — no STL written, because a")
        print("written STL is a claim that a part exists.\n")
        for i, err in enumerate(result.errors, 1):
            print(f"  attempt {i}: {err}")
        if result.code:
            print(f"\nlast code:\n{result.code}")
        return 1

    bx, by, bz = result.bbox_m
    print(f"built by {result.author} on attempt {result.attempts}")
    print(f"  mass    {result.mass_kg * 1000:.2f} g   ({args.material})")
    print(f"  volume  {result.volume_m3 * 1e9:.0f} mm^3")
    print(f"  bbox    {bx * 1000:.1f} x {by * 1000:.1f} x {bz * 1000:.1f} mm")
    for fmt, path in sorted(result.artifacts.items()):
        print(f"  {fmt:5s}   {path}")
    if not result.artifacts:
        print("  (no files written — pass --out to export)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
