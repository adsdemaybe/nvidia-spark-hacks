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

from engine.geometry.freeform import FreeformError
from engine.geometry.inspect import profile
from engine.geometry.registry import build
from engine.ir import CatalogueParam, GeometrySpec

DOCS_RAG_URL = "http://127.0.0.1:8220"

SYSTEM = """\
You write build123d code that produces one solid part.

Rules:
- Assign the finished solid to a variable named `part`. Nothing else is read.
- build123d is already imported (`from build123d import *`) and so is `math`. Do not
  write any import statement; there is no file, network or process access.
- Units are millimetres. build123d's native unit is mm and the harness converts.
- Produce a SOLID, not a sketch. A `Circle` is not a part; extrude or revolve it.
- Real dimensions. If the request implies a fastener, use its clearance size: M3 is
  3.4mm, M4 is 4.5mm, M5 is 5.5mm.

Reply with exactly one ```python code block and nothing else."""


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
) -> TextToPartResult:
    """Describe a part in words; get a measured solid or an honest failure.

    `ask(system, user) -> str` is the whole model interface, so this is testable with a
    stub and works against any backend.

    `max_attempts` is a budget, not a guarantee. Each retry carries the previous code and
    the error it raised — which is the only thing that makes retrying worth anything.
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
        return TextToPartResult(
            ok=True, code=code, attempts=attempt,
            mass_kg=mp.mass, volume_m3=mp.volume,
            bbox_m=(mp.bbox_size.x, mp.bbox_size.y, mp.bbox_size.z),
            errors=errors,
        )

    return TextToPartResult(ok=False, code=code, attempts=max_attempts, errors=errors)
