"""Acceptance-gate tests for the fixture robot/asset providers (Shadow Robot
Spatial Demonstration Pipeline spec section 52, Phases 2-3).
"""

from __future__ import annotations

import pytest
from spatial_providers import (
    FixtureAssetProvider,
    FixtureRobotProvider,
    UnknownAssetError,
    UnknownRobotError,
)

# ---------------------------------------------------------------------------
# FixtureRobotProvider — spec section 52 Phase 2 acceptance gate
# ---------------------------------------------------------------------------


def test_fixture_robot_provider_loads_default_bundle():
    bundle = FixtureRobotProvider().get_robot_bundle()
    # "so101" is the real robot now (Track A: swapped from a made-up
    # placeholder to the actual open-source SO-101,
    # github.com/TheRobotStudio/SO-ARM100), not "fixture_so101".
    assert bundle.manifest.robot_id == "so101"
    assert bundle.manifest.source == "fixture"
    assert bundle.urdf_path.exists()
    assert bundle.visual_glb_path.exists()


def test_urdf_referenced_by_manifest_parses():
    pin = pytest.importorskip("pinocchio")
    bundle = FixtureRobotProvider().get_robot_bundle()
    model = pin.buildModelFromUrdf(str(bundle.urdf_path))
    urdf_joint_names = {model.names[i] for i in range(1, len(model.names))}
    ir_joint_names = {j.name for j in bundle.robot_ir.joints if j.type != "fixed"}
    assert ir_joint_names <= urdf_joint_names


def test_robot_ir_end_effector_frame_matches_urdf():
    pin = pytest.importorskip("pinocchio")
    bundle = FixtureRobotProvider().get_robot_bundle()
    model = pin.buildModelFromUrdf(str(bundle.urdf_path))
    frame_id = model.getFrameId(bundle.robot_ir.end_effector_frame)
    assert frame_id < model.nframes


def test_capability_profile_arm_dof_is_five_plus_a_real_gripper():
    # The real SO-101 is a 5-DOF arm + a separate 1-DOF gripper joint (the
    # placeholder it replaced was 6-DOF with no gripper at all).
    bundle = FixtureRobotProvider().get_robot_bundle()
    assert bundle.capability_profile.arm_dof == 5
    assert bundle.capability_profile.end_effector == "parallel_gripper"
    assert bundle.capability_profile.finger_count == 2
    assert bundle.capability_profile.workspace_radius_m > 0


def test_unknown_robot_id_raises():
    with pytest.raises(UnknownRobotError):
        FixtureRobotProvider().get_robot_bundle("not_a_real_robot")


# ---------------------------------------------------------------------------
# FixtureAssetProvider — spec section 52 Phase 3 acceptance gate
# ---------------------------------------------------------------------------


def test_fixture_asset_provider_loads_button():
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    assert asset.asset_id == "button_01"


def test_button_interaction_part_validates():
    asset = FixtureAssetProvider().get_asset_bundle("button_01")
    part = asset.parts["button"]
    assert part.interaction == "press"
    assert part.travel_m == 0.006
    assert part.axis == (0.0, 0.0, -1.0)


def test_asset_glb_path_resolves_to_a_real_file():
    path = FixtureAssetProvider().asset_glb_path("button_01")
    assert path.exists()
    assert path.suffix == ".glb"


def test_unknown_asset_id_raises():
    with pytest.raises(UnknownAssetError):
        FixtureAssetProvider().get_asset_bundle("not_a_real_asset")


def test_fixture_asset_provider_loads_cube():
    asset = FixtureAssetProvider().get_asset_bundle("cube_01")
    part = asset.parts["cube"]
    assert part.interaction == "grasp"


def test_fixture_asset_provider_loads_drawer():
    asset = FixtureAssetProvider().get_asset_bundle("drawer_01")
    handle = asset.parts["handle"]
    drawer = asset.parts["drawer"]
    assert handle.interaction == "grasp"
    assert drawer.interaction == "pull"
    assert drawer.joint_type == "prismatic"
    assert drawer.limit_m == (0.0, 0.08)


def test_fixture_asset_provider_loads_soda_can():
    asset = FixtureAssetProvider().get_asset_bundle("soda_can_01")
    part = asset.parts["can"]
    assert part.interaction == "grasp"


def test_fixture_asset_provider_loads_keyboard():
    asset = FixtureAssetProvider().get_asset_bundle("keyboard_01")
    assert set(asset.parts) == {"key_A", "key_K", "key_T", "key_SPACE", "key_ENTER"}
    key_k = asset.parts["key_K"]
    assert key_k.interaction == "press"
    assert key_k.axis == (0.0, 0.0, -1.0)
    assert key_k.travel_m == 0.004
