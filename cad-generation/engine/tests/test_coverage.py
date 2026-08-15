from engine.coverage import analyze_coverage
from engine.examples import simple_rover


def test_coverage_covers_every_quantity_geometry_param():
    ir = simple_rover()
    coverage = analyze_coverage(ir)

    expected = {
        ("chassis", "length"),
        ("chassis", "width"),
        ("chassis", "thickness"),
        ("bracket_L", "arm_a_length"),
        ("bracket_L", "arm_b_length"),
        ("bracket_L", "thickness"),
        ("bracket_L", "width"),
        ("wheel_L", "outer_diameter"),
        ("wheel_L", "inner_diameter"),
        ("wheel_L", "length"),
    }
    assert {(c.link_id, c.param_name) for c in coverage} == expected


def test_coverage_responses_are_nonnegative_and_finite():
    coverage = analyze_coverage(simple_rover())
    for c in coverage:
        for name, response in c.responses.items():
            assert response >= 0.0
            assert response == response  # not NaN


def test_chassis_footprint_is_not_blind_to_static_margin():
    coverage = analyze_coverage(simple_rover())
    chassis_length = next(c for c in coverage if c.link_id == "chassis" and c.param_name == "length")
    assert chassis_length.responses.get("static_margin", 0.0) > 0.001
    assert not chassis_length.blind
