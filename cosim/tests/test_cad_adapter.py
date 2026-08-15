"""The CAD->cosim seam, tested against the shape the CAD side actually emits.

`from_cad_service` existed for a while and had never been run against a real `RobotIR`.
The first time it was, it raised `KeyError: 'name'` — from a function whose own docstring
promised it would report a missing field through `validate()` rather than throw. These
fixtures are copied from `simple_rover`'s real `model_dump(mode="json")`, so the contract
is pinned by an example rather than by a memory of one.
"""

from __future__ import annotations

import math

import pytest

from cosim.robot import from_cad_ir, from_cad_service


CAD_IR = {
    "id": "simple_rover",
    "name": "simple_rover",
    "root_link": "chassis",
    "links": [
        {"id": "chassis", "pose": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
         "geometry": {"generator": "plate", "params": {
             "length": {"value": 0.3, "unit": "m"},
             "width": {"value": 0.2, "unit": "m"},
             "thickness": {"value": 0.01, "unit": "m"}}}},
        {"id": "wheel_L", "pose": {"position": {"x": 0.1, "y": 0.03, "z": 0.0},
                                   "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
         "geometry": {"generator": "tube", "params": {}}},
    ],
    "joints": [
        {"id": "chassis_to_bracket", "kind": "fixed", "parent": "chassis",
         "child": "wheel_L", "origin": {"position": {"x": 0.1, "y": 0.03, "z": 0.01}},
         "axis": {"x": 0.0, "y": 0.0, "z": 1.0}, "limits": None, "actuator": None},
        {"id": "drive_L", "kind": "revolute", "parent": "chassis", "child": "wheel_L",
         "origin": {"position": {"x": 0.1, "y": 0.03, "z": 0.0}},
         "axis": {"x": 0.0, "y": 1.0, "z": 0.0},
         "limits": {"lower": {"value": -3.1416, "unit": "rad"},
                    "upper": {"value": 3.1416, "unit": "rad"}},
         "actuator": {"kind": "catalogue", "value": "nema17_direct",
                      "catalogue": "stepper_motors"}},
    ],
}

MASS = {
    "chassis": {"mass": 1.62, "com": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bbox_size": {"x": 0.3, "y": 0.2, "z": 0.01},
                "inertia": {"ixx": 5.4e-3, "iyy": 1.2e-2, "izz": 1.7e-2}},
    "wheel_L": {"mass": 0.0214, "com": {"x": 0.0, "y": 0.0, "z": 0.0},
                "bbox_size": {"x": 0.06, "y": 0.06, "z": 0.02},
                "inertia": {"ixx": 7.1e-6, "iyy": 7.1e-6, "izz": 9.6e-6}},
}


def test_the_real_cad_shape_maps_without_throwing():
    """The regression: `id` not `name`, `kind` not `type`, axis as a mapping."""
    spec = from_cad_ir(CAD_IR, MASS)
    assert [l.name for l in spec.links] == ["chassis", "wheel_L"]
    assert spec.validate() == []


def test_dispatch_recognises_a_cad_payload():
    """`from_cad_service` must route a CAD IR to the CAD adapter, not the flat one."""
    assert from_cad_service(CAD_IR).links[0].name == "chassis"


def test_a_fixed_joint_is_dropped_rather_than_invented_into_a_hinge():
    """A weld is not a degree of freedom, and guessing one adds motion the design forbids."""
    spec = from_cad_ir(CAD_IR, MASS)
    assert [j.name for j in spec.joints] == ["drive_L"]
    assert spec.joints[0].type == "hinge"


def test_mass_comes_from_cad_and_is_not_recomputed():
    spec = from_cad_ir(CAD_IR, MASS)
    assert spec.link("chassis").mass_kg == pytest.approx(1.62)
    assert spec.link("chassis").inertia_kgm2[0] == pytest.approx(5.4e-3)


def test_quantities_carry_units_and_a_wrong_one_is_refused():
    """A millimetre silently read as a metre is a 1000x error that looks merely odd."""
    bad = {**CAD_IR, "links": [{**CAD_IR["links"][0],
           "geometry": {"generator": "plate", "params": {
               "length": {"value": 300.0, "unit": "mm"},
               "width": {"value": 200.0, "unit": "mm"},
               "thickness": {"value": 10.0, "unit": "mm"}}}}]}
    with pytest.raises(ValueError, match="expected m"):
        from_cad_ir(bad, {})  # no mass supplied, so it must read the declared dims


def test_limits_are_read_through_their_quantity_wrapper():
    spec = from_cad_ir(CAD_IR, MASS)
    lo, hi = spec.joints[0].limit_rad
    assert (lo, hi) == pytest.approx((-math.pi, math.pi), abs=1e-3)


def test_the_catalogue_reference_survives_as_the_motor_id():
    """A name from their catalogue must arrive intact, not be swapped for a default.

    `nema17_direct` is not in cosim's motor catalogue. That must surface as a missing
    motor when a rollout asks for it — quietly substituting one that happens to run would
    simulate a robot nobody designed.
    """
    spec = from_cad_ir(CAD_IR, MASS)
    assert spec.actuators[0].motor_id == "nema17_direct"


def test_a_link_with_no_mass_is_flagged_rather_than_defaulted_silently():
    spec = from_cad_ir(CAD_IR, {})
    assert any(a.field_name == "mass_kg" for a in spec.assumptions)


def test_an_unknown_joint_kind_is_refused_rather_than_guessed():
    bad = {**CAD_IR, "joints": [{**CAD_IR["joints"][1], "kind": "helical"}]}
    with pytest.raises(ValueError, match="helical"):
        from_cad_ir(bad, MASS)
