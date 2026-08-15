"""Composition geometry contracts (envgen.cousins.compose)."""
from __future__ import annotations

import numpy as np
import pytest
import shapely.geometry as sg

from envgen.cousins.compose import footprint_at, reach_polygon, sample_point_in_polygon
from r2s.core.provenance import AssetProvenance, Proposer, assumed
from r2s.core.schemas import (
    AssetPayload,
    CollisionSummary,
    PhysicalProperties,
    RobotSpec,
)


def _asset(bbox=(0.4, 0.2, 0.1)) -> AssetPayload:
    return AssetPayload(
        asset_id="test_asset",
        label="box",
        category="box",
        provenance=AssetProvenance.PROCEDURAL,
        proposer=Proposer(kind="deterministic", detail="test"),
        bbox_m=bbox,
        collision=CollisionSummary(method="stub"),
        physics=PhysicalProperties(
            mass=assumed(0.1, "kg"),
            density=assumed(600.0, "kg/m^3"),
            com_local=assumed((0.0, 0.0, 0.05), "m"),
            inertia_diag=assumed((1e-4, 1e-4, 1e-4), "kg*m^2"),
            static_friction=assumed(0.5, "1"),
            dynamic_friction=assumed(0.4, "1"),
            restitution=assumed(0.1, "1"),
        ),
    )


class TestFootprintAt:
    def test_unrotated_extents(self):
        fp = footprint_at(_asset((0.4, 0.2, 0.1)), 0.0, 0.0, 0.0)
        minx, miny, maxx, maxy = fp.bounds
        assert (maxx - minx) == pytest.approx(0.4, abs=1e-9)
        assert (maxy - miny) == pytest.approx(0.2, abs=1e-9)

    def test_90deg_rotation_swaps_extents(self):
        fp = footprint_at(_asset((0.4, 0.2, 0.1)), 0.0, 0.0, np.pi / 2)
        minx, miny, maxx, maxy = fp.bounds
        assert (maxx - minx) == pytest.approx(0.2, abs=1e-9)
        assert (maxy - miny) == pytest.approx(0.4, abs=1e-9)

    def test_translation(self):
        fp = footprint_at(_asset((0.4, 0.2, 0.1)), 1.0, 2.0, 0.0)
        cx, cy = fp.centroid.x, fp.centroid.y
        assert cx == pytest.approx(1.0, abs=1e-9)
        assert cy == pytest.approx(2.0, abs=1e-9)

    def test_rotation_preserves_area(self):
        a0 = footprint_at(_asset(), 0.0, 0.0, 0.0).area
        a1 = footprint_at(_asset(), 0.5, -0.3, 0.7).area
        assert a1 == pytest.approx(a0, rel=1e-9)

    def test_rotated_translated_90deg(self):
        fp = footprint_at(_asset((0.4, 0.2, 0.1)), 1.0, 2.0, np.pi / 2)
        minx, miny, maxx, maxy = fp.bounds
        assert minx == pytest.approx(0.9, abs=1e-9)
        assert maxx == pytest.approx(1.1, abs=1e-9)
        assert miny == pytest.approx(1.8, abs=1e-9)
        assert maxy == pytest.approx(2.2, abs=1e-9)


class TestSamplePointInPolygon:
    L_SHAPE = sg.Polygon([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)])

    def test_all_draws_inside_l_shape(self):
        rng = np.random.default_rng(1234)
        tolerant = self.L_SHAPE.buffer(1e-9)
        for _ in range(200):
            pt = sample_point_in_polygon(self.L_SHAPE, rng)
            assert pt is not None
            assert tolerant.contains(sg.Point(pt)), f"{pt} landed outside the L"

    def test_covers_both_arms(self):
        # bbox-rejection failure mode: only ever sampling one lobe
        rng = np.random.default_rng(99)
        pts = [sample_point_in_polygon(self.L_SHAPE, rng) for _ in range(200)]
        right_arm = sum(1 for x, y in pts if x > 1.0)
        top_arm = sum(1 for x, y in pts if y > 1.0)
        assert right_arm > 10
        assert top_arm > 10

    def test_seeded_rng_reproducible(self):
        a = [
            sample_point_in_polygon(self.L_SHAPE, np.random.default_rng(7))
            for _ in range(1)
        ][0]
        b = [
            sample_point_in_polygon(self.L_SHAPE, np.random.default_rng(7))
            for _ in range(1)
        ][0]
        assert a == b

    def test_empty_polygon_returns_none(self):
        rng = np.random.default_rng(0)
        assert sample_point_in_polygon(sg.Polygon(), rng) is None

    def test_tiny_polygon_returns_none(self):
        rng = np.random.default_rng(0)
        micro = sg.Point(0, 0).buffer(1e-6)  # area ~3e-12 < 1e-8
        assert sample_point_in_polygon(micro, rng) is None


class TestReachPolygon:
    def test_annulus_at_base_height(self):
        robot = RobotSpec()  # base at origin, reach 0.08..0.35, band -0.05..0.40
        ring = reach_polygon(robot, surface_height=0.0)
        assert not ring.is_empty
        expected = np.pi * (robot.reach_max_m**2 - robot.reach_min_m**2)
        # buffer() polygons inscribe the circle, so area is slightly under
        assert ring.area == pytest.approx(expected, rel=0.05)
        # annulus: hole at the middle
        assert not ring.contains(sg.Point(0.0, 0.0))
        assert ring.contains(sg.Point(0.2, 0.0))
        assert not ring.contains(sg.Point(0.4, 0.0))

    def test_empty_when_surface_above_height_band(self):
        robot = RobotSpec()
        ring = reach_polygon(robot, surface_height=0.6)  # dz=0.6 > band hi 0.40
        assert ring.is_empty

    def test_empty_when_surface_below_height_band(self):
        robot = RobotSpec()
        ring = reach_polygon(robot, surface_height=-0.2)  # dz=-0.2 < band lo -0.05
        assert ring.is_empty

    def test_empty_when_dz_exceeds_reach_sphere(self):
        # dz inside the band but beyond reach_max: r_out^2 - dz^2 <= 0
        robot = RobotSpec(reach_max_m=0.35, height_band_m=(-0.05, 0.40))
        ring = reach_polygon(robot, surface_height=0.36)
        assert ring.is_empty

    def test_radius_shrinks_with_height(self):
        robot = RobotSpec()
        at_base = reach_polygon(robot, surface_height=0.0)
        raised = reach_polygon(robot, surface_height=0.25)
        assert 0 < raised.area < at_base.area

    def test_annulus_follows_base_pose(self):
        from r2s.core.geometry import Pose

        robot = RobotSpec(base_pose=Pose(position=(1.0, 2.0, 0.0)))
        ring = reach_polygon(robot, surface_height=0.0)
        assert ring.contains(sg.Point(1.2, 2.0))
        assert not ring.contains(sg.Point(0.0, 0.0))
