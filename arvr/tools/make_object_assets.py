#!/usr/bin/env python3
"""Generate the cube/drawer/soda_can/keyboard fixture InteractableAssets
(Shadow Robot Spatial Demonstration Pipeline spec section 14's recommended
demo progression: button [done, tools/make_button_asset.py] -> cube->bin ->
drawer pull -> soda_can->bin -> keyboard press, spec sections 20/24/26).

    uv run python tools/make_object_assets.py

Writes:
    fixtures/spatial-training/assets/cube/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/assets/drawer/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/assets/soda_can/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/assets/keyboard/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/props/bin.glb

The bin is deliberately NOT an InteractableAsset -- nothing in this
milestone presses/pulls/grasps a bin itself (InteractionKind has no
"is a static container" case, and inventing one would be dishonest about
what's actually modeled). It's a visual-only prop, the same role
spatialTeachMain.ts's button "stand" cylinder already plays -- the cube->bin
(and soda_can->bin) task's goal_position_m is just the bin's known world
location, matching how tools/make_mock_hand_episode.py already treats the
button's location as a plain world-space constant rather than something
derived from physics.

soda_can reuses cube's exact fixture shape (single "grasp" part, same
grasp-and-place TaskSpec predicate) -- it is a second rigid_graspable
object, not a new interaction kind, so it needed no new provider/contract
code (see STATE.md's Round 10). keyboard reuses button's exact shape (one
or more "press" parts, same reach-goal TaskSpec predicate already exercises
button) -- spec section 27's five named keys (A, K, T, SPACE, ENTER) are
modeled as five AssetParts on one asset; the demo task (spec section 63)
only exercises key_K, per spec section 42 ("do not require all keys").

Crude on purpose, same convention as tools/make_button_asset.py: small
deterministic primitives, not a reconstruction.
"""

from __future__ import annotations

import json
from pathlib import Path

import trimesh

ARVR_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "assets"
PROPS_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "props"

CUBE_SIZE_M = 0.03
DRAWER_FRONT_W_M = 0.10
DRAWER_FRONT_H_M = 0.05
DRAWER_FRONT_D_M = 0.01
HANDLE_RADIUS_M = 0.004
HANDLE_LENGTH_M = 0.03
BIN_OUTER_M = 0.08
BIN_HEIGHT_M = 0.03

# soda_can: a miniature can (same "crude on purpose, fits the fixture
# workspace" scale as cube's 0.03m edge, not a real 0.033x0.122m can --
# see CLAUDE.md's fixture-before-integration convention).
CAN_RADIUS_M = 0.018
CAN_HEIGHT_M = 0.07

# keyboard: a flat slab with five raised key caps for the spec's named keys
# (section 26: "start with only selected fully interactive keys"). Key caps
# are laid out along local X, evenly spaced, purely for a legible fixture --
# not a real keyboard layout.
KEYBOARD_W_M = 0.12
KEYBOARD_D_M = 0.05
KEYBOARD_H_M = 0.01
KEY_CAP_SIZE_M = 0.012
KEY_CAP_HEIGHT_M = 0.006
KEY_TRAVEL_M = 0.004
KEY_SPACING_M = 0.022
KEY_IDS = ("key_A", "key_K", "key_T", "key_SPACE", "key_ENTER")


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path.relative_to(ARVR_ROOT)}")


def write_glb(path: Path, scene: trimesh.Scene) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    glb = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
    path.write_bytes(glb)
    print(f"wrote {path.relative_to(ARVR_ROOT)} ({path.stat().st_size} bytes)")


def make_cube() -> None:
    bundle_dir = ASSETS_DIR / "cube"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": "cube_01",
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )
    write_json(
        bundle_dir / "interaction.json",
        {
            "schema_version": "1.0",
            "asset_id": "cube_01",
            "parts": {
                "cube": {
                    "interaction": "grasp",
                    "local_origin_m": [0.0, 0.0, CUBE_SIZE_M / 2.0],
                }
            },
        },
    )
    mesh = trimesh.creation.box(extents=(CUBE_SIZE_M, CUBE_SIZE_M, CUBE_SIZE_M))
    mesh.apply_translation((0.0, 0.0, CUBE_SIZE_M / 2.0))
    write_glb(bundle_dir / "asset.glb", trimesh.Scene(mesh))


def make_drawer() -> None:
    bundle_dir = ASSETS_DIR / "drawer"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": "drawer_01",
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )
    # Pull axis/limit are in the asset's own local frame (spec section 15) --
    # for this fixture the asset is placed at world identity orientation, so
    # local and world axes coincide (documented, not assumed generically).
    # -Y matches the direction verified reachable end-to-end against the
    # real SO-101 (see STATE.md's Round 9 for the exact IK-checked points).
    write_json(
        bundle_dir / "interaction.json",
        {
            "schema_version": "1.0",
            "asset_id": "drawer_01",
            "parts": {
                "handle": {
                    "interaction": "grasp",
                    "local_origin_m": [0.0, 0.0, DRAWER_FRONT_H_M / 2.0],
                },
                "drawer": {
                    "interaction": "pull",
                    "joint_type": "prismatic",
                    "axis": [0.0, -1.0, 0.0],
                    "limit_m": [0.0, 0.08],
                },
            },
        },
    )

    front_extents = (DRAWER_FRONT_D_M, DRAWER_FRONT_W_M, DRAWER_FRONT_H_M)
    front = trimesh.creation.box(extents=front_extents)
    front.apply_translation((0.0, 0.0, DRAWER_FRONT_H_M / 2.0))
    handle = trimesh.creation.cylinder(
        radius=HANDLE_RADIUS_M, height=HANDLE_LENGTH_M, sections=16,
    )
    handle.apply_transform(trimesh.transformations.rotation_matrix(1.5708, (1, 0, 0)))
    handle.apply_translation((-DRAWER_FRONT_D_M / 2.0 - 0.005, 0.0, DRAWER_FRONT_H_M / 2.0))
    write_glb(bundle_dir / "asset.glb", trimesh.Scene([front, handle]))


def make_soda_can() -> None:
    # Identical shape to make_cube()'s bundle -- a second rigid_graspable
    # object exercising the same grasp-and-place TaskSpec predicate, not a
    # new interaction kind (see module docstring).
    bundle_dir = ASSETS_DIR / "soda_can"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": "soda_can_01",
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )
    write_json(
        bundle_dir / "interaction.json",
        {
            "schema_version": "1.0",
            "asset_id": "soda_can_01",
            "parts": {
                "can": {
                    "interaction": "grasp",
                    "local_origin_m": [0.0, 0.0, CAN_HEIGHT_M / 2.0],
                }
            },
        },
    )
    mesh = trimesh.creation.cylinder(radius=CAN_RADIUS_M, height=CAN_HEIGHT_M, sections=24)
    mesh.apply_translation((0.0, 0.0, CAN_HEIGHT_M / 2.0))
    write_glb(bundle_dir / "asset.glb", trimesh.Scene(mesh))


def make_keyboard() -> None:
    # Identical shape to make_button_asset.py's bundle, repeated once per
    # named key -- five "press" AssetParts on one asset (see module
    # docstring), all sharing button's already-exercised reach-goal
    # TaskSpec predicate. The full visual keyboard is one static slab mesh
    # (spec section 26: "full visual keyboard may still be rendered") with
    # a raised cap per modeled key; unlisted keys are not physically
    # distinct geometry, matching spec section 42 ("do not require all
    # keys").
    bundle_dir = ASSETS_DIR / "keyboard"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": "keyboard_01",
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )

    n = len(KEY_IDS)
    offsets = [(i - (n - 1) / 2.0) * KEY_SPACING_M for i in range(n)]
    key_top_z = KEYBOARD_H_M + KEY_CAP_HEIGHT_M
    parts = {
        key_id: {
            "interaction": "press",
            "local_origin_m": [x, 0.0, key_top_z],
            "axis": [0.0, 0.0, -1.0],
            "travel_m": KEY_TRAVEL_M,
        }
        for key_id, x in zip(KEY_IDS, offsets, strict=True)
    }
    write_json(
        bundle_dir / "interaction.json",
        {"schema_version": "1.0", "asset_id": "keyboard_01", "parts": parts},
    )

    slab = trimesh.creation.box(extents=(KEYBOARD_W_M, KEYBOARD_D_M, KEYBOARD_H_M))
    slab.apply_translation((0.0, 0.0, KEYBOARD_H_M / 2.0))
    caps = []
    for x in offsets:
        cap = trimesh.creation.box(
            extents=(KEY_CAP_SIZE_M, KEY_CAP_SIZE_M, KEY_CAP_HEIGHT_M),
        )
        cap.apply_translation((x, 0.0, KEYBOARD_H_M + KEY_CAP_HEIGHT_M / 2.0))
        caps.append(cap)
    write_glb(bundle_dir / "asset.glb", trimesh.Scene([slab, *caps]))


def make_bin_prop() -> None:
    # Solid shallow tray, not a hollow box -- same "crude on purpose"
    # convention as every other fixture mesh; a visual placeholder, not a
    # claim of real collision geometry (nothing collides with the bin in
    # this milestone's checks).
    mesh = trimesh.creation.box(extents=(BIN_OUTER_M, BIN_OUTER_M, BIN_HEIGHT_M))
    mesh.apply_translation((0.0, 0.0, BIN_HEIGHT_M / 2.0))
    write_glb(PROPS_DIR / "bin.glb", trimesh.Scene(mesh))


def main() -> None:
    make_cube()
    make_drawer()
    make_soda_can()
    make_keyboard()
    make_bin_prop()


if __name__ == "__main__":
    main()
