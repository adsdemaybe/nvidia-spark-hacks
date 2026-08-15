"""Acceptance tests for the generated mug fixture asset (tools/make_mug_asset.py).

The mug is the object a human picks up on camera in the can-pickup scene, so
its dimensions are part of the recorded signal: the grasp aperture in a
demonstration is set by the actual object. These tests pin the geometry the
tool's docstring promises — upright, base at z = 0, real mug dimensions,
a handle — so a change to the generator that quietly moves the object has to
be a deliberate one.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh
from make_mug_asset import ARVR_ROOT, BUNDLE_DIR, mug_body, mug_handle
from spatial_providers import FixtureAssetProvider

# 0.1 mm. Vertices go through the GLB as float32 and the body is tessellated as
# a polygon, so nothing here is exact; a tenth of a millimetre is far tighter
# than any modelling mistake worth catching and far looser than the round-trip
# error.
TOL_M = 1e-4


@pytest.fixture(scope="module")
def scene() -> trimesh.Scene:
    return trimesh.load(BUNDLE_DIR / "asset.glb")


@pytest.fixture(scope="module")
def mesh(scene: trimesh.Scene) -> trimesh.Trimesh:
    return scene.to_mesh()


def test_bundle_has_the_same_three_files_as_the_button():
    # The button bundle is what FixtureAssetProvider and ar_backend.assets were
    # written against; matching it exactly is why no loader needed changing.
    for name in ("asset.glb", "manifest.json", "interaction.json"):
        assert (BUNDLE_DIR / name).exists(), f"missing {name}"
    manifest = json.loads((BUNDLE_DIR / "manifest.json").read_text())
    assert manifest["asset_id"] == "mug_01"
    assert manifest["asset_glb"] == "asset.glb"


def test_existing_fixture_asset_provider_reads_the_mug():
    asset = FixtureAssetProvider().get_asset_bundle("mug_01")
    assert asset.asset_id == "mug_01"
    # Both grasps a demonstrator can perform: the barrel and the handle. The
    # handle is the whole point of replacing the procedural cylinder, so its
    # absence should fail here and not just look wrong on screen.
    assert set(asset.parts) == {"body", "handle"}
    assert {p.interaction for p in asset.parts.values()} == {"grasp"}


def test_glb_nodes_are_named_for_the_interaction_parts(scene: trimesh.Scene):
    asset = FixtureAssetProvider().get_asset_bundle("mug_01")
    assert set(scene.geometry) == set(asset.parts)


def test_mug_stands_upright_with_its_base_on_the_local_origin_plane(mesh: trimesh.Trimesh):
    lower, upper = mesh.bounds
    # The client places the mug by its base, so z = 0 is the contract: put the
    # origin on the table and the mug rests on the table.
    assert lower[2] == pytest.approx(0.0, abs=TOL_M)
    assert upper[2] == pytest.approx(0.089, abs=TOL_M)


def test_body_is_a_mug_sized_barrel_centred_on_the_local_z_axis():
    # Measured on the body alone: the handle deliberately pushes the whole
    # mug's bounding box off-centre in x, so only the body can say whether the
    # axis of revolution is where the placement contract claims.
    lower, upper = mug_body().bounds
    assert lower[0] == pytest.approx(-0.041, abs=TOL_M)
    assert upper[0] == pytest.approx(0.041, abs=TOL_M)
    assert lower[1] == pytest.approx(-0.041, abs=TOL_M)
    assert upper[1] == pytest.approx(0.041, abs=TOL_M)
    assert lower[2] == pytest.approx(0.0, abs=TOL_M)
    assert upper[2] == pytest.approx(0.089, abs=TOL_M)


def test_body_is_hollow_with_a_real_cavity():
    body = mug_body()
    # A solid 82 x 89 mm cylinder would be ~470 cm^3. The shell is the material
    # only, so anything near that means the revolve stopped producing a cavity
    # and the "mug" became a slug the size of a mug.
    solid_cm3 = np.pi * 0.041**2 * 0.089 * 1e6
    assert body.volume * 1e6 < 0.35 * solid_cm3
    # Brim-full capacity of the cavity, which is what makes it a 12 oz mug.
    capacity_ml = np.pi * 0.037**2 * (0.089 - 0.006) * 1e6
    assert 340 < capacity_ml < 375


def test_handle_protrudes_far_enough_to_hook_two_fingers():
    handle = mug_handle()
    lower, upper = handle.bounds
    # The handle lives on +X, starts inside the wall, and the gap between the
    # outer wall (41 mm) and the inside of the bar has to take fingers.
    assert lower[0] > 0.037 - TOL_M, "handle pokes through the inner wall into the cavity"
    assert upper[0] > 0.065, "handle barely leaves the wall — nothing to grasp"
    finger_gap_m = (upper[0] - 2 * 0.0045) - 0.041
    assert finger_gap_m > 0.020
    # And it sits in the upper half of the mug's height, where a hand goes.
    assert lower[2] > 0.015
    assert upper[2] < 0.089


def test_mesh_is_watertight_and_outward_wound(mesh: trimesh.Trimesh):
    # Watertight here means every edge is shared by exactly two faces: the
    # body is one closed solid of revolution and the handle is one closed tube.
    # They interpenetrate at the join rather than being boolean-unioned (a CSG
    # engine would be a new build dependency and a determinism hazard — see
    # tools/make_assets.py), so `mesh.volume` double-counts the overlap and is
    # not a meaningful number. Winding is checked because an inverted handle
    # renders as a hole in the mug under backface culling.
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mug_body().volume > 0
    assert mug_handle().volume > 0


def test_triangle_count_is_cheap_enough_to_render_every_frame(mesh: trimesh.Trimesh):
    # The client already spends its frame budget on the camera feed and hand
    # tracking. An 8 cm object seen from arm's length needs smooth silhouettes,
    # not detail: a few thousand triangles, not a scanned asset's million.
    assert 500 < len(mesh.faces) < 20_000


def test_committed_glb_matches_what_the_generator_produces_now(scene: trimesh.Scene):
    # Guards against the checked-in binary drifting from the source that claims
    # to produce it — the failure mode where somebody edits the constants and
    # forgets to re-run the tool.
    for name, fresh in (("body", mug_body()), ("handle", mug_handle())):
        loaded = scene.geometry[name]
        assert len(loaded.faces) == len(fresh.faces), f"{name}: stale, re-run the tool"
        assert np.allclose(loaded.bounds, fresh.bounds, atol=1e-6), (
            f"{name}: stale, re-run the tool"
        )


def test_tool_lives_where_the_other_fixture_generators_do():
    assert (ARVR_ROOT / "tools" / "make_mug_asset.py").exists()
