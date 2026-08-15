import math

import pytest

from engine.geometry.registry import build, generators
from engine.ir import CatalogueParam, GeometrySpec, Provenance, Quantity


def _q(value: float, unit: str = "m") -> Quantity:
    return Quantity(value=value, unit=unit, provenance=Provenance(status="ASSUMED", source=""))


def _material(key: str = "aluminum_6061") -> CatalogueParam:
    return CatalogueParam(value=key, catalogue="materials")


def test_registry_has_builtin_generators():
    assert {"tube", "plate", "bracket"} <= set(generators())


def test_plate_mass_properties():
    spec = GeometrySpec(
        generator="plate",
        params={"length": _q(0.30), "width": _q(0.20), "thickness": _q(0.01)},
        material=_material(),
    )
    result = build(spec)
    mp = result.mass_properties

    expected_volume = 0.30 * 0.20 * 0.01
    assert mp.volume == pytest.approx(expected_volume, rel=1e-6)

    density = 2700.0  # aluminum_6061, kg/m^3
    assert mp.mass == pytest.approx(expected_volume * density, rel=1e-6)

    assert mp.bbox_min.as_tuple() == pytest.approx((0.0, -0.10, 0.0), abs=1e-9)
    assert mp.bbox_max.as_tuple() == pytest.approx((0.30, 0.10, 0.01), abs=1e-9)
    assert mp.com.as_tuple() == pytest.approx((0.15, 0.0, 0.005), abs=1e-6)


def test_tube_mass_properties():
    spec = GeometrySpec(
        generator="tube",
        params={"outer_diameter": _q(0.06), "inner_diameter": _q(0.05), "length": _q(0.02)},
        material=_material("pla"),
    )
    result = build(spec)
    mp = result.mass_properties

    expected_volume = math.pi * ((0.03**2) - (0.025**2)) * 0.02
    assert mp.volume == pytest.approx(expected_volume, rel=1e-3)
    assert mp.com.as_tuple() == pytest.approx((0.0, 0.0, 0.01), abs=1e-6)


def test_tube_rejects_inner_ge_outer():
    spec = GeometrySpec(
        generator="tube",
        params={"outer_diameter": _q(0.05), "inner_diameter": _q(0.06), "length": _q(0.02)},
        material=_material(),
    )
    with pytest.raises(ValueError):
        build(spec)


def test_bracket_builds_and_is_l_shaped():
    spec = GeometrySpec(
        generator="bracket",
        params={
            "arm_a_length": _q(0.05),
            "arm_b_length": _q(0.08),
            "thickness": _q(0.005),
            "width": _q(0.03),
        },
        material=_material(),
    )
    result = build(spec)
    mp = result.mass_properties

    horizontal_vol = 0.05 * 0.03 * 0.005
    vertical_vol = 0.005 * 0.03 * (0.08 - 0.005)
    assert mp.volume == pytest.approx(horizontal_vol + vertical_vol, rel=1e-6)
    assert mp.bbox_max.z == pytest.approx(0.08, abs=1e-9)
    assert mp.bbox_max.x == pytest.approx(0.05, abs=1e-9)


def test_missing_required_param_raises():
    spec = GeometrySpec(generator="plate", params={"length": _q(0.1)}, material=_material())
    with pytest.raises(KeyError):
        build(spec)


def test_unknown_generator_raises():
    spec = GeometrySpec(generator="nope", params={}, material=_material())
    with pytest.raises(KeyError):
        build(spec)


def test_bracket_rejects_an_arm_shorter_than_its_own_thickness():
    """Otherwise OCCT raises a bare `Standard_DomainError` with no message."""
    spec = GeometrySpec(
        generator="bracket",
        material=CatalogueParam(value="pla", catalogue="materials"),
        params={
            "arm_a_length": _q(0.10),
            "arm_b_length": _q(0.003),  # shorter than thickness
            "thickness": _q(0.005),
            "width": _q(0.02),
        },
    )
    with pytest.raises(ValueError, match="must exceed thickness"):
        build(spec)
