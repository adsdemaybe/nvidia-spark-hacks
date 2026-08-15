"""Physics prior inference contracts (scan.assetize.physics)."""
from __future__ import annotations

import trimesh

from r2s.core.provenance import ValueProvenance, inferred
from scan.assetize.physics import MASS_CEIL_KG, MASS_FLOOR_KG, infer_physics, inertia_valid


def _box(extents=(0.1, 0.1, 0.1)) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=extents)


class TestInferPhysics:
    def test_mass_positive_and_within_bounds(self):
        props = infer_physics(_box(), "box")
        assert props.mass.value > 0
        assert MASS_FLOOR_KG <= props.mass.value <= MASS_CEIL_KG
        assert props.mass.unit == "kg"

    def test_box_mass_matches_prior_arithmetic(self):
        # 0.1^3 m^3 * 600 kg/m^3 * 0.25 hollow = 0.15 kg
        props = infer_physics(_box((0.1, 0.1, 0.1)), "box")
        assert abs(props.mass.value - 0.15) < 1e-6

    def test_mass_is_inferred_with_method(self):
        props = infer_physics(_box(), "box")
        assert props.mass.provenance is ValueProvenance.INFERRED
        assert props.mass.method  # non-empty
        assert "watertight_mesh_volume" in props.mass.method

    def test_friction_is_assumed(self):
        props = infer_physics(_box(), "box")
        assert props.static_friction.provenance is ValueProvenance.ASSUMED
        assert props.dynamic_friction.provenance is ValueProvenance.ASSUMED
        assert 0 < props.static_friction.value <= 1.5
        assert props.dynamic_friction.value < props.static_friction.value

    def test_nothing_claims_confirmed_or_measured(self):
        props = infer_physics(_box(), "mug")
        for m in (
            props.mass,
            props.density,
            props.com_local,
            props.inertia_diag,
            props.static_friction,
            props.dynamic_friction,
            props.restitution,
        ):
            assert m.provenance in (ValueProvenance.INFERRED, ValueProvenance.ASSUMED)

    def test_unknown_category_uses_default_prior(self):
        props = infer_physics(_box(), "flux_capacitor")
        assert props.material_guess == "unknown"
        assert props.mass.value > 0

    def test_tiny_mesh_clipped_to_mass_floor(self):
        props = infer_physics(_box((0.001, 0.001, 0.001)), "box")
        assert props.mass.value == MASS_FLOOR_KG

    def test_inertia_valid_on_inferred_box(self):
        props = infer_physics(_box((0.1, 0.2, 0.05)), "box")
        valid, why = inertia_valid(props)
        assert valid, why

    def test_inertia_valid_across_categories(self):
        for cat in ("mug", "bottle", "book", "table"):
            valid, why = inertia_valid(infer_physics(_box(), cat))
            assert valid, f"{cat}: {why}"


class TestInertiaValid:
    def _fabricate(self, diag):
        props = infer_physics(_box(), "box")
        return props.model_copy(
            update={"inertia_diag": inferred(diag, "kg*m^2", method="fabricated_for_test")}
        )

    def test_catches_triangle_inequality_violation(self):
        # a + b < c: physically impossible for any rigid body
        valid, why = inertia_valid(self._fabricate((1.0, 1.0, 5.0)))
        assert not valid
        assert "triangle inequality" in why

    def test_catches_non_positive_moment(self):
        valid, why = inertia_valid(self._fabricate((0.0, 1.0, 1.0)))
        assert not valid
        assert "non-positive" in why

    def test_catches_negative_moment(self):
        valid, why = inertia_valid(self._fabricate((-1.0, 1.0, 1.0)))
        assert not valid
        assert "non-positive" in why

    def test_degenerate_equality_case_allowed(self):
        # a + b == c exactly (a flat plate) passes with the 0.999 tolerance
        valid, _ = inertia_valid(self._fabricate((1.0, 1.0, 2.0)))
        assert valid
