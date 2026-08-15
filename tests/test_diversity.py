"""Set-level diversity metric contracts (envgen.cousins.diversity)."""
from __future__ import annotations

import pytest

from envgen.cousins.diversity import min_distance_to_set, scene_distance
from r2s.core.artifacts import ArtifactRef
from r2s.core.geometry import Pose
from r2s.core.provenance import AssetProvenance
from r2s.core.schemas import Placement, RobotSpec, ScenePayload

ROOM_DIAG = 5.0

_SHELL = ArtifactRef(
    uri="cas://sha256/" + "0" * 64,
    sha256="0" * 64,
    size_bytes=1,
    media_type="application/json",
)


def _placement(instance_id, asset_id, category, xyz, role="distractor") -> Placement:
    return Placement(
        instance_id=instance_id,
        asset_id=asset_id,
        asset_provenance=AssetProvenance.PROCEDURAL,
        category=category,
        pose=Pose(position=xyz),
        role=role,
        prim_path=f"/World/{instance_id}",
    )


def _scene(scene_id, placements) -> ScenePayload:
    return ScenePayload(
        scene_id=scene_id,
        kind="COUSIN",
        cousin_index=0,
        split="train",
        seed=0,
        shell_ref=_SHELL,
        render_mode="pure_splat",
        robot=RobotSpec(),
        placements=placements,
    )


def _scene_a(scene_id="a", extra=()):
    return _scene(
        scene_id,
        [
            _placement("mug_0", "asset_mug", "mug", (0.2, 0.3, 0.8)),
            _placement("box_0", "asset_box", "box", (1.0, 0.5, 0.8)),
            *extra,
        ],
    )


def _scene_b_disjoint(scene_id="b", extra=()):
    return _scene(
        scene_id,
        [
            _placement("lamp_0", "asset_lamp", "lamp", (3.0, 3.0, 0.8)),
            _placement("book_0", "asset_book", "book", (2.5, 3.5, 0.8)),
            *extra,
        ],
    )


class TestSceneDistance:
    def test_identical_scenes_near_zero(self):
        d = scene_distance(_scene_a("a"), _scene_a("b"), room_diag=ROOM_DIAG)
        assert d == pytest.approx(0.0, abs=1e-6)

    def test_disjoint_scenes_far_apart(self):
        d = scene_distance(_scene_a(), _scene_b_disjoint(), room_diag=ROOM_DIAG)
        assert d > 0.5

    def test_symmetric(self):
        a, b = _scene_a(), _scene_b_disjoint()
        d_ab = scene_distance(a, b, room_diag=ROOM_DIAG)
        d_ba = scene_distance(b, a, room_diag=ROOM_DIAG)
        assert d_ab == pytest.approx(d_ba, abs=1e-9)

    def test_shared_assets_moved_layout_gives_partial_distance(self):
        moved = _scene(
            "m",
            [
                _placement("mug_0", "asset_mug", "mug", (3.0, 3.0, 0.8)),
                _placement("box_0", "asset_box", "box", (2.5, 3.5, 0.8)),
            ],
        )
        d = scene_distance(_scene_a(), moved, room_diag=ROOM_DIAG)
        # asset and category terms are zero; only the 0.35 layout term fires
        assert 0.05 < d <= 0.35 + 1e-6


class TestFixtureExclusion:
    def test_shared_fixture_does_not_lower_disjoint_distance(self):
        fixture = _placement(
            "shell_table", "asset_shell_table", "table", (1.5, 1.5, 0.0), role="fixture"
        )
        d_bare = scene_distance(_scene_a(), _scene_b_disjoint(), room_diag=ROOM_DIAG)
        d_fixt = scene_distance(
            _scene_a(extra=[fixture]),
            _scene_b_disjoint(extra=[fixture]),
            room_diag=ROOM_DIAG,
        )
        assert d_fixt == pytest.approx(d_bare, abs=1e-9)
        assert d_fixt > 0.5

    def test_fixture_excluded_from_asset_ids(self):
        fixture = _placement("f", "asset_f", "table", (0, 0, 0), role="fixture")
        s = _scene_a(extra=[fixture])
        assert "asset_f" not in s.asset_ids()


class TestMinDistanceToSet:
    def test_empty_accepted_set_is_max_novelty(self):
        assert min_distance_to_set(_scene_a(), [], room_diag=ROOM_DIAG) == 1.0

    def test_min_over_set(self):
        cand = _scene_a("cand")
        accepted = [_scene_a("twin"), _scene_b_disjoint("far")]
        d = min_distance_to_set(cand, accepted, room_diag=ROOM_DIAG)
        # nearest neighbour is the identical twin
        assert d == pytest.approx(0.0, abs=1e-6)
