"""Fit checking must catch real interference, not just wave designs through.

Each test below constructs a board/enclosure pair that is wrong in exactly one
way and asserts the specific violation code fires with a sane magnitude. A
checker that only ever returns ok=True is indistinguishable from no checker.
"""

from __future__ import annotations

import pytest

from cad_api.contracts import (
    BoardReport,
    Box3,
    ComponentHeight,
    ConnectorEdge,
    EnclosureIntent,
    EnclosureReport,
    FitResult,
    Keepout,
    MountingHole,
    Outline,
    Point2,
    PortCutout,
    Standoff,
    ThermalHotspot,
    Violation,
)
from cad_api.enclosure import design_enclosure
from cad_api.fit import check_fit, constrain_board


def simple_board(**kw) -> BoardReport:
    base = dict(
        outline_mm=Outline.rect(60, 40),
        thickness_mm=1.6,
        mounting_holes=[MountingHole(x_mm=4, y_mm=4, diameter_mm=3.2)],
    )
    base.update(kw)
    return BoardReport(**base)


def codes(result: FitResult) -> set[str]:
    return {v.code for v in result.violations}


# --- the happy path is a real design, not a stub ------------------------


def test_generated_enclosure_fits_its_own_board():
    board = simple_board(
        component_heightmap=[ComponentHeight(ref="C1", x_mm=10, y_mm=10, width_mm=5, depth_mm=5, height_mm=8.0)],
        connector_edges=[ConnectorEdge(ref="J1", edge="east", x_mm=20, y_mm=20, width_mm=9, height_mm=3.5)],
    )
    result = design_enclosure(board, EnclosureIntent(), run_evaluate=False)
    assert result.fit.ok, [v.model_dump() for v in result.fit.violations]
    assert result.fit.violations == []


def test_design_is_deterministic():
    """Same input twice -> identical hash. The artifact cache depends on this."""
    board = simple_board()
    a = design_enclosure(board, EnclosureIntent(), run_evaluate=False)
    b = design_enclosure(board, EnclosureIntent(), run_evaluate=False)
    assert a.enclosure_report.artifacts.content_hash == b.enclosure_report.artifacts.content_hash
    assert a.enclosure_report.cavity_mm == b.enclosure_report.cavity_mm


# --- negative cases -----------------------------------------------------


def test_board_larger_than_cavity_is_a_blocker():
    small = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    oversized = simple_board(outline_mm=Outline.rect(200, 40))
    result = check_fit(oversized, small)
    assert not result.ok
    assert "board_exceeds_cavity" in codes(result)
    v = next(v for v in result.violations if v.code == "board_exceeds_cavity")
    assert v.measured == pytest.approx(200.0)
    assert v.overshoot > 0


def test_tall_component_exceeds_cavity_height():
    short = design_enclosure(simple_board(), EnclosureIntent(headroom_mm=0.0), run_evaluate=False).enclosure_report
    tall = simple_board(
        component_heightmap=[ComponentHeight(ref="C1", x_mm=10, y_mm=10, width_mm=5, depth_mm=5, height_mm=50.0)]
    )
    result = check_fit(tall, short)
    assert "stack_exceeds_cavity_height" in codes(result)
    v = next(v for v in result.violations if v.code == "stack_exceeds_cavity_height")
    # 4 standoff + 1.6 board + 50 component
    assert v.measured == pytest.approx(55.6, abs=1e-6)


def test_mounting_hole_without_standoff():
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    moved = simple_board(mounting_holes=[MountingHole(x_mm=50, y_mm=30, diameter_mm=3.2)])
    result = check_fit(moved, enc)
    assert "mounting_hole_unsupported" in codes(result)


def test_mounting_hole_diameter_mismatch_is_minor():
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    fatter = simple_board(mounting_holes=[MountingHole(x_mm=4, y_mm=4, diameter_mm=5.0)])
    result = check_fit(fatter, enc)
    assert "mounting_hole_diameter_mismatch" in codes(result)
    v = next(v for v in result.violations if v.code == "mounting_hole_diameter_mismatch")
    assert v.severity == "minor"
    # minor alone must not fail the design
    assert result.ok


def test_connector_without_cutout_is_a_blocker():
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    with_conn = simple_board(
        connector_edges=[ConnectorEdge(ref="J9", edge="east", x_mm=20, y_mm=20, width_mm=9, height_mm=3.5)]
    )
    result = check_fit(with_conn, enc)
    assert "connector_without_cutout" in codes(result)
    assert not result.ok


def test_cutout_on_wrong_edge():
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    enc.port_cutouts = [PortCutout(ref="J1", edge="north", x_mm=10, y_mm=10, width_mm=9, height_mm=3.5)]
    board = simple_board(
        connector_edges=[ConnectorEdge(ref="J1", edge="east", x_mm=20, y_mm=20, width_mm=9, height_mm=3.5)]
    )
    result = check_fit(board, enc)
    assert "cutout_wrong_edge" in codes(result)


def test_cutout_too_small():
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    enc.port_cutouts = [PortCutout(ref="J1", edge="east", x_mm=10, y_mm=10, width_mm=2.0, height_mm=3.5)]
    board = simple_board(
        connector_edges=[ConnectorEdge(ref="J1", edge="east", x_mm=20, y_mm=20, width_mm=9, height_mm=3.5)]
    )
    result = check_fit(board, enc)
    assert "cutout_too_small" in codes(result)


def test_standoff_intruding_into_keepout():
    """A *bottom*-side keepout is the one a standoff can actually reach."""
    board = simple_board(
        keepouts=[Keepout(name="antenna", x_mm=2, y_mm=2, width_mm=6, depth_mm=6, side="bottom")]
    )
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    result = check_fit(board, enc)
    assert "standoff_in_keepout" in codes(result)
    v = next(v for v in result.violations if v.code == "standoff_in_keepout")
    assert v.measured > 0  # actual intrusion depth in mm


def test_top_side_keepout_is_not_intruded_by_a_standoff():
    """The same geometry on the top side must NOT be flagged.

    Standoffs live entirely under the board, so a top-side keepout cannot be
    intruded by one. This previously reported a blocker, which in the §6
    negotiation is worse than a missed violation: it drives a re-place that
    cannot converge, because there is nothing to fix.
    """
    board = simple_board(
        keepouts=[Keepout(name="antenna", x_mm=2, y_mm=2, width_mm=6, depth_mm=6, side="top")]
    )
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    result = check_fit(board, enc)
    assert "standoff_in_keepout" not in codes(result)
    assert result.ok


def test_sealed_enclosure_flags_thermal_but_only_as_minor():
    board = simple_board(thermal_hotspots=[ThermalHotspot(ref="U2", x_mm=30, y_mm=20, power_w=12.0)])
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    assert enc.port_cutouts == []  # sealed
    result = check_fit(board, enc)
    assert "sealed_enclosure_thermal_risk" in codes(result)
    v = next(v for v in result.violations if v.code == "sealed_enclosure_thermal_risk")
    assert v.severity == "minor"
    assert result.ok  # a flag, not a verdict — must not block the design


def test_vented_board_does_not_flag_thermal():
    board = simple_board(
        thermal_hotspots=[ThermalHotspot(ref="U2", x_mm=30, y_mm=20, power_w=12.0)],
        connector_edges=[ConnectorEdge(ref="J1", edge="east", x_mm=20, y_mm=20, width_mm=9, height_mm=3.5)],
    )
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    result = check_fit(board, enc)
    assert "sealed_enclosure_thermal_risk" not in codes(result)


def test_violations_accumulate_rather_than_short_circuit():
    """The negotiator needs the whole picture to decide which side moves."""
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    bad = simple_board(
        outline_mm=Outline.rect(500, 400),
        mounting_holes=[MountingHole(x_mm=100, y_mm=100, diameter_mm=3.2)],
        connector_edges=[ConnectorEdge(ref="JX", edge="north", x_mm=5, y_mm=5, width_mm=4, height_mm=4)],
    )
    result = check_fit(bad, enc)
    assert len(codes(result)) >= 3


# --- FitResult invariant -------------------------------------------------


def test_fitresult_cannot_claim_ok_with_blocking_violations():
    with pytest.raises(ValueError):
        FitResult(
            ok=True,
            violations=[
                Violation(code="x", severity="blocker", detail="", measured=1, limit=0, unit="mm")
            ],
        )


# --- constrain_board -----------------------------------------------------


def test_constrain_board_returns_a_usable_envelope():
    board = simple_board()
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    env = constrain_board("board overhangs the north wall", enc, EnclosureIntent())

    assert env.reason
    # The envelope is expressed in BOARD coordinates: the original 60x40 board
    # started at (0,0), so the usable region must start there too.
    assert env.max_outline_mm.min_x_mm == pytest.approx(0.0, abs=1e-9)
    assert env.max_outline_mm.min_y_mm == pytest.approx(0.0, abs=1e-9)
    assert env.max_outline_mm.length_mm == pytest.approx(60.0, abs=1e-9)
    assert env.max_outline_mm.width_mm == pytest.approx(40.0, abs=1e-9)
    assert env.max_component_height_mm > 0
    assert len(env.mounting_hole_pattern) == len(enc.standoff_positions)
    assert env.mounting_hole_pattern[0].x_mm == pytest.approx(4.0, abs=1e-9)
    assert env.mounting_hole_pattern[0].y_mm == pytest.approx(4.0, abs=1e-9)


def test_envelope_keepouts_cover_the_standoffs():
    board = simple_board()
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    env = constrain_board("clearance", enc, EnclosureIntent())
    assert len(env.keepouts) == len(enc.standoff_positions)
    ko = env.keepouts[0]
    assert ko.width_mm == pytest.approx(enc.standoff_positions[0].outer_diameter_mm)


def test_board_shrunk_to_the_envelope_then_fits():
    """The negotiation actually converges: constrain -> shrink -> re-check passes."""
    enc = design_enclosure(simple_board(), EnclosureIntent(), run_evaluate=False).enclosure_report
    oversized = simple_board(outline_mm=Outline.rect(200, 300))
    assert not check_fit(oversized, enc).ok

    env = constrain_board("too big", enc, EnclosureIntent())
    shrunk = simple_board(
        outline_mm=Outline.rect(env.max_outline_mm.length_mm, env.max_outline_mm.width_mm)
    )
    assert check_fit(shrunk, enc).ok


def test_board_with_no_mounting_holes_is_flagged_but_not_blocked():
    """Every other check passes and the board still rattles — say so."""
    board = BoardReport(outline_mm=Outline.rect(60, 40), mounting_holes=[])
    enc = design_enclosure(board, EnclosureIntent(), run_evaluate=False).enclosure_report
    assert enc.standoff_positions == []
    result = check_fit(board, enc)
    assert "board_not_mechanically_secured" in codes(result)
    assert result.ok  # minor: snap-fit designs are legitimate


# --- regressions: checks that existed in the contract but not in the checker ---


def test_bottom_component_taller_than_standoff_is_a_blocker():
    """A board that cannot physically seat must not pass.

    `_check_height` only ever measured the stack above the board, so a 12mm
    electrolytic underneath, on 4mm standoffs, returned ok=True with zero
    violations while reaching the cavity floor 8mm before the standoffs did.
    """
    board = simple_board(
        component_heightmap=[
            ComponentHeight(ref="C9", x_mm=20, y_mm=20, width_mm=10, depth_mm=10,
                            height_mm=12.0, side="bottom"),
        ]
    )
    # An enclosure built to the intent's 4mm standoffs, as an external caller may supply.
    enc = EnclosureReport(
        cavity_mm=Box3(length_mm=63, width_mm=43, height_mm=10.6),
        standoff_positions=[
            Standoff(x_mm=h.x_mm + 1.5, y_mm=h.y_mm + 1.5, height_mm=4.0,
                     hole_diameter_mm=h.diameter_mm, outer_diameter_mm=6.0)
            for h in board.mounting_holes
        ],
        wall_thickness_mm=2.0,
        max_component_height_mm=0.0,
        board_origin_mm=Point2(x_mm=1.5, y_mm=1.5),
    )
    result = check_fit(board, enc)
    assert "bottom_component_exceeds_standoff" in codes(result)
    assert not result.ok
    v = next(v for v in result.violations if v.code == "bottom_component_exceeds_standoff")
    assert v.measured == pytest.approx(12.0)
    assert v.limit == pytest.approx(4.0)


def test_generated_enclosure_raises_standoffs_to_clear_bottom_components():
    """design_enclosure must produce an enclosure the board actually fits in."""
    board = simple_board(
        component_heightmap=[
            ComponentHeight(ref="C9", x_mm=20, y_mm=20, width_mm=10, depth_mm=10,
                            height_mm=12.0, side="bottom"),
        ]
    )
    res = design_enclosure(board, EnclosureIntent(standoff_height_mm=4.0), run_evaluate=False)
    assert res.enclosure_report.standoff_positions[0].height_mm >= 12.0
    assert res.fit.ok, [v.code for v in res.fit.violations]


def test_board_positioned_outside_the_cavity_is_caught():
    """Fits by extent, but hangs over the wall."""
    board = simple_board(outline_mm=Outline.rect(60, 40))
    enc = EnclosureReport(
        cavity_mm=Box3(length_mm=63, width_mm=43, height_mm=20),
        standoff_positions=[],
        wall_thickness_mm=2.0,
        max_component_height_mm=0.0,
        board_origin_mm=Point2(x_mm=20.0, y_mm=1.5),  # shoved 20mm right
    )
    result = check_fit(board, enc)
    assert "board_overhangs_cavity" in codes(result)
    assert not result.ok


def test_reports_contain_no_non_finite_numbers():
    """`Infinity` is not valid JSON and fails JSON.parse on the TypeScript side."""
    import json
    import math

    board = simple_board(mounting_holes=[MountingHole(x_mm=5, y_mm=5, diameter_mm=3.2)])
    enc = EnclosureReport(
        cavity_mm=Box3(length_mm=63, width_mm=43, height_mm=20),
        standoff_positions=[],  # nothing to measure a distance to
        wall_thickness_mm=2.0,
        max_component_height_mm=0.0,
    )
    result = check_fit(board, enc)
    assert "mounting_hole_unsupported" in codes(result)
    for v in result.violations:
        assert math.isfinite(v.measured), f"{v.code} measured={v.measured}"
        assert math.isfinite(v.limit), f"{v.code} limit={v.limit}"
    blob = result.model_dump_json()
    json.loads(blob, parse_constant=_reject)


def _reject(const: str):
    raise AssertionError(f"non-standard JSON constant in report: {const}")
