"""Model-written build123d: execution, failure modes, and the checks that matter.

Every case here is something that actually happened while testing against a local model,
not a hypothetical. The one worth reading is
`test_a_blind_bore_is_caught_though_every_bulk_measurement_passes`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine.geometry.freeform import FreeformError, check_source, run_to_step  # noqa: E402
from engine.geometry.inspect import profile  # noqa: E402
from engine.geometry.registry import build, generators  # noqa: E402
from engine.ir import CatalogueParam, GeometrySpec  # noqa: E402
from engine.text_to_cad import check_expectations, extract_code, text_to_part  # noqa: E402

MAT = CatalogueParam(kind="catalogue", value="pla", catalogue="materials")
PLA_DENSITY = 1240.0


def make(code: str, **params):
    return build(GeometrySpec(generator="freeform", params={"code": code, **params}, material=MAT))


def reply(code: str) -> str:
    return f"```python\n{code}\n```"


# --- the generator is a generator like any other ---------------------------------


def test_freeform_is_registered():
    assert "freeform" in generators()


def test_a_box_measures_exactly():
    """30 x 20 x 10 mm of PLA is 6000 mm³ and 7.44 g, and nothing here should round it."""
    mp = make("part = Box(30, 20, 10)").mass_properties
    assert mp.volume * 1e9 == pytest.approx(6000.0, rel=1e-6)
    assert mp.mass == pytest.approx(6000e-9 * PLA_DENSITY, rel=1e-6)


def test_the_builder_api_works_not_only_the_algebra_api():
    """Models write both; `BuildPart` needs unwrapping to a shape and the algebra form does not."""
    mp = make(
        "with BuildPart() as bp:\n"
        "    Box(20, 20, 5)\n"
        "    Hole(radius=2)\n"
        "part = bp"
    ).mass_properties
    assert mp.volume * 1e9 < 20 * 20 * 5  # the hole removed something
    assert mp.volume > 0


# --- failure modes, each one observed ---------------------------------------------


def test_code_that_never_binds_part_says_so():
    with pytest.raises(FreeformError, match="did not bind"):
        make("x = Box(10, 10, 10)")


def test_a_sketch_is_not_a_part():
    """`part = Circle(10)` exports as valid STEP and then has no mass.

    Left alone it surfaces as a pydantic validation error about `mass` several layers
    down, which tells the model nothing about geometry.
    """
    with pytest.raises(FreeformError, match="no solid"):
        make("part = Circle(10)")


def test_an_empty_boolean_is_not_a_part():
    with pytest.raises(FreeformError, match="no solid"):
        make("part = Box(10, 10, 10) - Box(20, 20, 20)")


def test_filesystem_and_process_access_are_refused():
    for bad in ("import os\npart = Box(1,1,1)",
                "import subprocess\npart = Box(1,1,1)",
                "f = open('/etc/passwd')\npart = Box(1,1,1)"):
        with pytest.raises(FreeformError, match="not available"):
            check_source(bad)


def test_a_runaway_is_killed_rather_than_hanging_the_parent():
    """OCC can also segfault; a child dying is survivable and the parent dying is not."""
    with pytest.raises(FreeformError, match="did not finish"):
        run_to_step("while True:\n    pass\npart = Box(1,1,1)", timeout_s=5)


# --- the finding: bulk properties cannot see a wrong feature -----------------------


def test_a_blind_bore_is_caught_though_every_bulk_measurement_passes():
    """The failure that motivated `inspect.py`.

    Asked for a 12mm spacer with a 5.2mm through bore, a local model wrote an outer
    cylinder at the default CENTER alignment (z ∈ [-4, 4]) and a MIN-aligned cutter
    (z ∈ [0, 8]). They overlap for 4mm, so the bore stops halfway.

    Every bulk number is *correct for the solid that was built*: bounding box exactly
    12 x 12 x 8, mass 1.02 g matching its 819.8 mm³ to two decimals. It is simply not the
    part that was asked for, and only a question about cross-sections can tell.
    """
    blind = make(
        "part = Cylinder(6, 8) - Cylinder(2.6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))"
    )
    mp = blind.mass_properties

    # the measurements that do *not* notice
    assert mp.bbox_size.x * 1000 == pytest.approx(12, abs=0.01)
    assert mp.bbox_size.z * 1000 == pytest.approx(8, abs=0.01)
    assert mp.mass * 1000 == pytest.approx(1.02, abs=0.01)

    # the one that does
    pr = profile(blind.part, "Z")
    assert not pr.bore_is_through
    assert pr.near_holes == 0 and pr.far_holes == 1

    problems = check_expectations(blind.part, {"through_bore": "Z"})
    assert problems and "does not go through" in problems[0]


def test_a_real_through_bore_passes_the_same_check():
    good = make("part = Cylinder(6, 8) - Cylinder(2.6, 8)")
    pr = profile(good.part, "Z")
    assert pr.bore_is_through
    assert pr.near_area_mm2 == pytest.approx(pr.far_area_mm2, rel=1e-6)
    assert good.mass_properties.volume * 1e9 == pytest.approx(
        math.pi * (6**2 - 2.6**2) * 8, rel=1e-6
    )
    assert check_expectations(good.part, {"through_bore": "Z"}) == []


def test_a_solid_with_no_bore_is_not_mistaken_for_a_through_one():
    solid = make("part = Cylinder(6, 8)")
    assert not profile(solid.part, "Z").bore_is_through


def test_volume_and_bbox_expectations_are_checked():
    p = make("part = Box(30, 20, 10)").part
    assert check_expectations(p, {"bbox_mm": (30, 20, 10), "volume_mm3": 6000}) == []
    bad = check_expectations(p, {"bbox_mm": (30, 20, 12), "volume_mm3": 9000})
    assert len(bad) == 2


# --- the loop ---------------------------------------------------------------------


def test_a_failure_is_retried_with_the_reason_attached():
    """Retrying without the error is just resampling; the error is what makes it a loop."""
    seen: list[str] = []
    replies = [reply("part = Circle(20)"), reply("part = Box(40, 30, 10)")]

    def ask(system, user):
        seen.append(user)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    r = text_to_part("a plate", ask, docs=False, max_attempts=3)
    assert r.ok and r.attempts == 2
    assert "It failed with:" in seen[1]
    assert "Circle(20)" in seen[1], "the retry must carry the code that failed"
    assert "no solid" in seen[1]


def test_the_loop_repairs_a_blind_bore_when_the_gate_reports_it():
    want = math.pi * (6**2 - 2.6**2) * 8
    n = {"i": 0}

    def ask(system, user):
        n["i"] += 1
        if n["i"] == 1:
            return reply("part = Cylinder(6, 8) - Cylinder(2.6, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))")
        return reply("part = Cylinder(6, 8) - Cylinder(2.6, 8)")

    r = text_to_part("12mm spacer, 5.2mm bore, 8mm tall", ask, docs=False, max_attempts=3,
                     expect={"through_bore": "Z", "volume_mm3": want})
    assert r.ok and r.attempts == 2
    assert r.volume_m3 * 1e9 == pytest.approx(want, rel=1e-6)
    assert "does not go through" in r.errors[0]


def test_giving_up_reports_every_attempt_rather_than_the_last():
    r = text_to_part("a plate", lambda s, u: reply("part = Circle(1)"), docs=False, max_attempts=2)
    assert not r.ok
    assert len(r.errors) == 2


def test_code_is_extracted_from_a_fence_or_taken_whole():
    assert extract_code("```python\npart = 1\n```") == "part = 1"
    assert extract_code("```\npart = 2\n```") == "part = 2"
    assert extract_code("part = 3") == "part = 3"


# --- counting bores -----------------------------------------------------
#
# All three of these are the same L-bracket request, judged three times. The
# model got it wrong three different ways and the harness accepted the first
# two, which is the argument for the check rather than an illustration of it.


def _bracket(holes_at: tuple[float, ...]) -> object:
    """A 60x5x5 bar with M5 clearance holes bored at the given x positions.

    x=45 is off the end of a bar centred on the origin, which is exactly the
    mistake worth encoding: the model wrote a hole there and it cut nothing.
    """
    cuts = " ".join(f"- Pos({x}, 0, 0) * Cylinder(2.75, 20)" for x in holes_at)
    return make(f"part = Box(60, 5, 5) {cuts}").part


def test_a_missing_bore_is_caught_though_the_bounding_box_is_identical():
    """The failure this check exists for. Asked for two M5 holes, the model put
    the `Hole()` calls after the `with BuildPart()` block closed, so they cut
    nothing. The solid built, measured, and had exactly the bounding box the
    request implies — with or without holes."""
    undrilled = make("part = Box(60, 5, 5)").part
    problems = check_expectations(undrilled, {"bores": {"diameter_mm": 5.5, "count": 2}})
    assert problems and "found 0" in problems[0]
    # and the bbox check it slipped past says nothing is wrong
    assert not check_expectations(undrilled, {"bbox_mm": (60, 5, 5)})


def test_two_faces_of_one_bore_are_not_two_bores():
    """The second wrong answer. One hole drilled and one placed off the part
    gave two cylindrical faces, because a through-hole is routinely split into
    two half-cylinders. Counting faces accepted it; counting axes does not."""
    one_hole = _bracket((15.0,))
    cylinders = [f for f in one_hole.faces() if str(f.geom_type) == "GeomType.CYLINDER"]
    assert len(cylinders) >= 1
    problems = check_expectations(one_hole, {"bores": {"diameter_mm": 5.5, "count": 2}})
    assert problems and "found 1" in problems[0]


def test_two_real_bores_pass():
    assert not check_expectations(_bracket((15.0, -15.0)), {"bores": {"diameter_mm": 5.5, "count": 2}})


def test_a_bore_of_the_wrong_diameter_does_not_count():
    """An M3 clearance hole is not an M5 one, and the message says which
    diameters are actually present so the retry has somewhere to go."""
    m3 = make("part = Box(60, 5, 5) - Pos(15, 0, 0) * Cylinder(1.7, 20)").part
    problems = check_expectations(m3, {"bores": {"diameter_mm": 5.5, "count": 1}})
    assert problems and "3.4" in problems[0]


# --- export: the step that used to be missing ------------------------------


def test_a_freeform_part_can_actually_be_exported(tmp_path):
    """The regression that hid behind STL working.

    Every freeform part is an imported STEP — `run_to_step` builds in a
    subprocess and returns a file — so the object this module measures has
    always been through `import_step`. `export_step` raises a bare
    `RuntimeError: Failed to write STEP file` for such a shape while writing an
    identically-shaped freshly-built one fine. STL was unaffected, which made it
    look like a STEP quirk rather than "the freeform path cannot export at all".
    """
    from engine.text_to_cad import code_to_part

    r = code_to_part("part = Box(20, 10, 5)", out_stem=tmp_path / "brick")
    assert r.ok, r.errors
    assert set(r.artifacts) == {"stl", "step"}
    for path in r.artifacts.values():
        assert Path(path).stat().st_size > 0


def test_the_exported_stl_is_watertight(tmp_path):
    """A mesh with an unshared edge slices into a shell. Checked here rather
    than trusted, because the exporter reports success either way."""
    import struct
    from collections import Counter

    from engine.text_to_cad import code_to_part

    r = code_to_part(
        "part = Box(20, 20, 6) - Pos(0, 0, 0) * Cylinder(3, 30)",
        out_stem=tmp_path / "washer",
    )
    assert r.ok, r.errors

    data = Path(r.artifacts["stl"]).read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    edges: Counter = Counter()
    for i in range(count):
        off = 84 + i * 50
        tri = [
            tuple(round(c, 4) for c in struct.unpack("<3f", data[off + 12 + j * 12 : off + 24 + j * 12]))
            for j in range(3)
        ]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[frozenset((tri[a], tri[b]))] += 1

    unshared = [e for e, n in edges.items() if n != 2]
    assert not unshared, f"{len(unshared)} edges not shared by exactly two faces"


def test_nothing_is_written_when_the_part_did_not_build(tmp_path):
    """A written STL is a claim that a part exists. Half a part on disk is the
    thing somebody sends to a printer at 2am."""
    from engine.text_to_cad import code_to_part

    r = code_to_part("part = Circle(5)", out_stem=tmp_path / "not_a_part")
    assert not r.ok
    assert r.artifacts == {}
    assert not list(tmp_path.iterdir())


def test_author_written_code_gets_no_privileges(tmp_path):
    """`code_to_part` is the path for code written in a chat session. It goes
    through the identical gates — the author proposes, the harness disposes."""
    from engine.text_to_cad import code_to_part

    r = code_to_part(
        "part = Box(20, 20, 6)",
        expect={"bores": {"diameter_mm": 5.5, "count": 2}},  # there are none
        out_stem=tmp_path / "unbored",
    )
    assert not r.ok
    assert r.artifacts == {}
    assert any("bore" in e for e in r.errors)


def test_a_misspelled_expectation_raises_instead_of_checking_nothing(tmp_path):
    """Found the hard way: `--expect '{"through_bores": 7}'` (plural) was accepted,
    checked nothing, and reported a gate that had not run. A silently-ignored
    expectation is the failure this whole function exists to prevent, wearing a
    pass as a disguise."""
    from engine.text_to_cad import check_expectations
    from build123d import Box

    with pytest.raises(ValueError) as exc:
        check_expectations(Box(10, 10, 10), {"through_bores": 7})
    assert "unknown expectation key" in str(exc.value)
    assert "through_bore" in str(exc.value)


def test_export_is_opt_in(tmp_path):
    # A function that writes files whether or not you asked cannot be called
    # inside a search loop.
    from engine.text_to_cad import code_to_part

    r = code_to_part("part = Box(10, 10, 10)")
    assert r.ok
    assert r.artifacts == {}
