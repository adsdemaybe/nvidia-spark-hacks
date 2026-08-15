"""§7: the robot <-> pcb-ai seam, both directions, and the golden round trip.

§7.4 asks for "a golden round-trip test (a reference robot spec -> board spec ->
`pcb-ai --model stub` -> ingest) in CI on both repos". The middle step is a
minutes-long subprocess and a full toolchain, so what runs here is the round trip
with a recorded `pcb-ai` output standing in for the run — the contract is
exercised end to end, the design loop is not. `test_the_emitted_envelope_is_the_real_contract`
is what stops that stand-in drifting from the real schema: it validates against
`cad_api.contracts`, which is the paired half of `pcb-ai/src/cad/contracts.ts`
and is required to change in the same commit.
"""

from __future__ import annotations

import json

import pytest

from engine.evaluate import evaluate
from engine.examples import powered_rover
from engine.export.board_spec import emit, envelope, markdown, spec_hash
from engine.ingest.pcb_run import apply, board_facts, gate_status
from engine.ir import RobotIR


# --- down: robot -> board spec ------------------------------------------


def test_the_emitted_envelope_is_the_real_contract():
    """Validated against the contract file, not against a copy of its shape.

    `cad_api.contracts.Envelope` is the Python half of the PCB<->CAD contract and
    is required to change in the same commit as its TypeScript mirror. Parsing
    through it means a field rename on either side breaks this test rather than
    producing an envelope `pcb-ai` silently rejects at intake.
    """
    from cad_api.contracts import Envelope

    parsed = Envelope.model_validate(envelope(powered_rover(), "motor_carrier"))
    assert parsed.max_outline_mm.max_x_mm == 60.0
    assert parsed.max_component_height_mm == 12.0
    assert len(parsed.mounting_hole_pattern) == 4
    assert parsed.max_bottom_component_height_mm == 0.0


def test_a_keepout_we_cannot_draw_travels_as_words_not_as_a_zero_area_rectangle():
    """A zero-area keepout is not a weaker keepout — it constrains nothing.

    The contract's `Keepout` is a rectangle with positive width and depth. The
    IR holds keepouts as reasons, because the swept volume is tier-2 work that
    does not exist. Emitting them as 0x0 rectangles satisfies the schema's shape
    and reads downstream as "this board has no keepouts", which is worse than
    saying nothing. So they go in `reason`, where the intake agent must act on
    them.
    """
    env = envelope(powered_rover(), "motor_carrier")
    assert env["keepouts"] == []
    assert "MECHANICAL KEEPOUTS" in env["reason"]
    assert "left wheel" in env["reason"]


def test_the_envelope_is_json_serialisable_as_written():
    # It crosses the boundary as a file. A dict that cannot be dumped is a
    # contract that works in tests and fails at the subprocess.
    json.dumps(envelope(powered_rover(), "motor_carrier"))


def test_the_spec_hash_ignores_mechanical_changes_the_board_cannot_see():
    """§4: a board respin costs minutes, so the cache key has to be narrow.

    Hashing the whole IR would respin the board every time a bracket got 2 mm
    longer, the cache would never hit, and the "never in the inner loop" rule
    would be decorative.
    """
    base = powered_rover()
    before = spec_hash(base, "motor_carrier")

    data = base.model_dump()
    wheel = next(link for link in data["links"] if link["id"] == "wheel_L")
    wheel["geometry"]["params"]["outer_diameter"]["value"] = 0.075

    assert spec_hash(RobotIR.model_validate(data), "motor_carrier") == before


def test_the_spec_hash_moves_when_the_board_constraints_move():
    base = powered_rover()
    before = spec_hash(base, "motor_carrier")

    data = base.model_dump()
    data["electronics"]["rails"][0]["budget_current"]["value"] = 9.0

    assert spec_hash(RobotIR.model_validate(data), "motor_carrier") != before


def test_a_board_with_no_mounting_pattern_is_called_a_defect_not_left_silent():
    """The defect the full-stack run found: no rover board had mounting holes,
    so every enclosure generated zero standoffs. Saying it in the spec is
    cheaper than finding it again."""
    base = powered_rover()
    data = base.model_dump()
    data["electronics"]["boards"][0]["mount"] = None
    text = markdown(RobotIR.model_validate(data), "motor_carrier")
    assert "defect in the robot design" in text


def test_the_spec_states_the_margin_separately_from_the_budget():
    text = markdown(powered_rover(), "motor_carrier")
    assert "1.25x" in text
    assert "worst case" in text and "with margin" in text


def test_the_spec_names_a_load_whose_current_the_catalogue_does_not_state():
    # The budget is a lower bound when a load is unpriceable, and a fab sizing
    # traces to it needs to be told, not left to notice.
    base = powered_rover()
    data = base.model_dump()
    # A servo with neither stall nor rated current in the catalogue.
    joint = next(j for j in data["joints"] if j["id"] == "bracket_to_wheel")
    joint["actuator"] = {"kind": "catalogue", "value": "feetech_st3215_7v4", "catalogue": "servos"}
    text = markdown(RobotIR.model_validate(data), "motor_carrier")
    assert "**unknown**" in text
    assert "lower bound" in text


def test_emitting_a_board_spec_for_a_robot_with_no_electronics_refuses():
    from engine.examples import simple_rover

    with pytest.raises(ValueError) as exc:
        emit(simple_rover(), "motor_carrier")
    assert "no electronics subsystem" in str(exc.value)


# --- up: pcb-ai run -> robot IR -----------------------------------------


def _board_report(width_mm: float = 55.0, height_mm: float = 35.0) -> dict:
    """A board report shaped exactly as `deriveBoardReport` emits one."""
    return {
        "design_id": "motor_carrier",
        "outline_mm": {
            "points": [
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": width_mm, "y_mm": 0.0},
                {"x_mm": width_mm, "y_mm": height_mm},
                {"x_mm": 0.0, "y_mm": height_mm},
            ]
        },
        "thickness_mm": 1.6,
        "mounting_holes": [{"x_mm": 4.0, "y_mm": 4.0, "diameter_mm": 3.2, "plated": False}],
        "component_heightmap": [
            {"ref": "U1", "x_mm": 20.0, "y_mm": 15.0, "width_mm": 6.0, "depth_mm": 6.0, "height_mm": 1.2, "side": "top"},
            {"ref": "J1", "x_mm": 27.0, "y_mm": 2.0, "width_mm": 10.0, "depth_mm": 5.0, "height_mm": 8.5, "side": "top"},
        ],
        "connector_edges": [
            {"ref": "J1", "edge": "south", "x_mm": 27.0, "y_mm": 2.0, "width_mm": 10.0, "height_mm": 8.5, "needs_cutout": True}
        ],
        "keepouts": [],
        "thermal_hotspots": [{"ref": "U1", "x_mm": 20.0, "y_mm": 15.0, "power_w": 1.4, "max_temp_c": None}],
        "mass": {
            "total_g": 11.6,
            "substrate_g": 5.1,
            "components_g": 6.5,
            "com_mm": {"x_mm": 26.0, "y_mm": 14.0},
        },
    }


def _summary(accepted: bool = True, **iteration) -> dict:
    base = {"index": 0, "compiled": True, "hard_failures": 0, "drc_errors": 0, "dfm_errors": 0}
    base.update(iteration)
    return {"iterations": [base], "accepted": accepted}


def test_the_golden_round_trip():
    """robot spec -> board spec -> (recorded pcb-ai output) -> ingest.

    What it proves: the two files this side emits and the run directory it reads
    back describe the same board, and a measured fact ends up where a criterion
    reads it — which is the only claim §7 actually makes.
    """
    ir = powered_rover()

    artifacts = emit(ir, "motor_carrier")
    assert artifacts.spec_hash
    assert "motor-driver carrier" in artifacts.markdown

    # The board was designed inside the envelope this side asked for.
    max_x = artifacts.envelope["max_outline_mm"]["max_x_mm"]
    report = _board_report(width_mm=max_x - 5.0)

    updated = apply(ir, board_facts("motor_carrier", report, _summary(), "runs/motor-carrier"))

    board = updated.electronics.board("motor_carrier")
    assert board.gate_status == "PASS"
    assert board.measured_mass.provenance.status == "MEASURED"
    assert board.measured_mass.magnitude_in("kg") == pytest.approx(0.0116)
    assert board.measured_dissipation.magnitude_in("W") == pytest.approx(1.4)

    # And the criteria that were silent now have something to check.
    results = {r.name: r for r in evaluate(updated, max_tier=0).results}
    assert results["board_fits_bay[motor_carrier]"].passed
    assert results["board_fits_bay[motor_carrier]"].provenance == "MEASURED"
    assert results["board_gate_passed"].passed
    assert "board_thermal_budget[motor_carrier]" in results


def test_ingest_returns_a_new_ir_rather_than_mutating_one():
    # §12 #8: revisions are immutable. Ingesting board facts is a revision.
    ir = powered_rover()
    updated = apply(ir, board_facts("motor_carrier", _board_report(), _summary(), "runs/x"))
    assert ir.electronics.board("motor_carrier").gate_status == "NOT_RUN"
    assert updated.electronics.board("motor_carrier").gate_status == "PASS"
    assert updated is not ir


def test_a_board_that_outgrew_its_bay_fails_by_millimetres_not_by_boolean():
    ir = powered_rover()
    # The bay is 60 x 40; the board came back 64 wide.
    facts = board_facts("motor_carrier", _board_report(width_mm=64.0), _summary(), "runs/x")
    results = {r.name: r for r in evaluate(apply(ir, facts), max_tier=0).results}
    fit = results["board_fits_bay[motor_carrier]"]
    assert not fit.passed
    assert fit.magnitude == pytest.approx(-4.0)
    assert "on x" in fit.detail


def test_a_failing_board_gate_is_a_failing_robot():
    """§12 non-negotiable #10, and the reason it is a criterion and not an
    exception: `evaluate()` takes no argument about which failures to ignore."""
    ir = powered_rover()
    facts = board_facts("motor_carrier", _board_report(), _summary(accepted=False, drc_errors=3), "runs/x")
    updated = apply(ir, facts)

    assert updated.electronics.board("motor_carrier").gate_status == "FAIL"
    report = evaluate(updated, max_tier=0)
    assert not report.passed
    assert not {r.name: r for r in report.results}["board_gate_passed"].passed


def test_gate_status_reads_the_runs_own_verdict_rather_than_recomputing_it():
    assert gate_status(_summary(accepted=True))[0] == "PASS"
    assert gate_status(_summary(accepted=False))[0] == "FAIL"
    assert gate_status(_summary(compiled=False))[0] == "FAIL"
    assert "did not compile" in gate_status(_summary(compiled=False))[1]
    assert gate_status({"iterations": []})[0] == "FAIL"


def test_harness_length_stops_being_a_guess():
    """§7.3: connector positions from Circuit JSON, so the drop term is real."""
    ir = powered_rover()
    before = ir.electronics.harnesses[0]
    assert before.length.provenance.status == "INFERRED"
    assert "estimated before the board exists" in before.length.provenance.note

    facts = board_facts("motor_carrier", _board_report(), _summary(), "runs/motor-carrier")
    updated = apply(ir, facts)
    after = updated.electronics.harnesses[0]

    assert after.length.value != pytest.approx(before.length.value)
    assert "connector J1" in after.length.provenance.note
    assert after.length.provenance.source == "runs/motor-carrier"


def test_a_board_report_with_no_outline_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError) as exc:
        board_facts("motor_carrier", {"outline_mm": {"points": []}}, _summary(), "runs/x")
    assert "no usable outline" in str(exc.value)
