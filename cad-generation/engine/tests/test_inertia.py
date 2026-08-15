"""Inertia tensors, and the OpenCascade reference-frame trap behind them.

Tier 2 cannot run without these: MuJoCo rejects a non-realisable inertia at
model-compile time, and the error names the body rather than the reason.
"""

import numpy as np
import pytest

from engine.evaluate import compute_mass_properties, evaluate
from engine.examples import simple_rover
from engine.geometry.registry import build
from engine.ir import CatalogueParam, GeometrySpec, Provenance, Quantity


def _q(value: float, unit: str = "m") -> Quantity:
    return Quantity(value=value, unit=unit, provenance=Provenance(status="ASSUMED", source=""))


def _material(key: str = "aluminum_6061") -> CatalogueParam:
    return CatalogueParam(value=key, catalogue="materials")


def _plate(length: float, width: float, thickness: float):
    return build(
        GeometrySpec(
            generator="plate",
            params={"length": _q(length), "width": _q(width), "thickness": _q(thickness)},
            material=_material(),
        )
    )


def test_plate_inertia_matches_the_analytic_box():
    """A box's inertia about its own centre is m/12*(a^2+b^2) per axis. Closed
    form, so there is no excuse for being approximately right."""
    length, width, thickness = 0.30, 0.16, 0.006
    mp = _plate(length, width, thickness).mass_properties
    m = mp.mass

    assert mp.inertia.ixx == pytest.approx(m / 12 * (width**2 + thickness**2), rel=1e-9)
    assert mp.inertia.iyy == pytest.approx(m / 12 * (length**2 + thickness**2), rel=1e-9)
    assert mp.inertia.izz == pytest.approx(m / 12 * (length**2 + width**2), rel=1e-9)
    # A box aligned with its own axes has no products of inertia.
    assert mp.inertia.ixy == pytest.approx(0.0, abs=1e-15)
    assert mp.inertia.ixz == pytest.approx(0.0, abs=1e-15)
    assert mp.inertia.iyz == pytest.approx(0.0, abs=1e-15)


def test_inertia_is_referenced_to_the_centre_of_mass_not_the_origin():
    """The trap this module exists for.

    `GProp_GProps.MatrixOfInertia()` is CoM-referenced. A plate built with one
    corner at the origin has its CoM 150 mm away along X, so an origin-referenced
    tensor would carry a parallel-axis term of m*d^2 — two orders of magnitude
    larger here. If this ever starts failing, every downstream inertia is wrong
    and MuJoCo will be rejecting bodies for reasons that look unrelated.
    """
    length, width = 0.30, 0.16
    mp = _plate(length, width, 0.006).mass_properties
    assert mp.com.x == pytest.approx(length / 2, rel=1e-9)  # geometry is corner-origin

    about_com = mp.mass / 12 * (length**2 + width**2)
    about_origin = about_com + mp.mass * (length / 2) ** 2  # parallel-axis shift

    assert mp.inertia.izz == pytest.approx(about_com, rel=1e-9)
    assert mp.inertia.izz != pytest.approx(about_origin, rel=1e-3)


def test_tube_inertia_is_hollow_not_solid():
    """The bore is absent from the collider by design, but it must not be
    absent from the mass model — a wheel simulated as a solid disc has the
    wrong rotational inertia and accelerates wrong under the same torque."""
    outer, inner, length = 0.09, 0.06, 0.03
    hollow = build(
        GeometrySpec(
            generator="tube",
            params={"outer_diameter": _q(outer), "inner_diameter": _q(inner), "length": _q(length)},
            material=_material(),
        )
    ).mass_properties

    m, ro, ri = hollow.mass, outer / 2, inner / 2
    # Thick-walled cylinder about its axis of symmetry: m/2*(ro^2 + ri^2).
    assert hollow.inertia.izz == pytest.approx(m / 2 * (ro**2 + ri**2), rel=1e-6)
    # Strictly greater than the solid-disc value m/2*ro^2 would be at this mass.
    assert hollow.inertia.izz > m / 2 * ro**2


def test_every_generator_produces_a_realisable_tensor():
    """Positive-definite and obeying A + B >= C, for every link of a real design."""
    for link_id, mp in compute_mass_properties(simple_rover()).items():
        a, b, c = mp.inertia.principal_moments()
        assert a > 0, f"{link_id} has a non-positive principal moment"
        assert a + b >= c - 1e-12, f"{link_id} violates the triangle inequality"


def test_inertia_valid_criterion_runs_for_every_link():
    ir = simple_rover()
    report = evaluate(ir, max_tier=0)
    named = {r.name for r in report.results}
    for link in ir.links:
        assert f"inertia_valid[{link.id}]" in named
    assert all(r.passed for r in report.results if r.name.startswith("inertia_valid"))


def test_as_matrix_is_symmetric():
    mp = _plate(0.2, 0.1, 0.01).mass_properties
    matrix = mp.inertia.as_matrix()
    assert np.allclose(matrix, matrix.T)
