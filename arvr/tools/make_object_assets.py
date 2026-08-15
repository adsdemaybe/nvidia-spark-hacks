#!/usr/bin/env python3
"""Generate the cube/drawer fixture InteractableAssets (Shadow Robot Spatial
Demonstration Pipeline spec section 14's recommended demo progression:
button [done, tools/make_button_asset.py] -> cube->bin -> drawer pull).

    uv run python tools/make_object_assets.py

Writes:
    fixtures/spatial-training/assets/cube/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/assets/drawer/{manifest,interaction}.json + asset.glb
    fixtures/spatial-training/props/bin.glb

The bin is deliberately NOT an InteractableAsset -- nothing in this
milestone presses/pulls/grasps a bin itself (InteractionKind has no
"is a static container" case, and inventing one would be dishonest about
what's actually modeled). It's a visual-only prop, the same role
spatialTeachMain.ts's button "stand" cylinder already plays -- the cube->bin
task's goal_position_m is just the bin's known world location, matching how
tools/make_mock_hand_episode.py already treats the button's location as a
plain world-space constant rather than something derived from physics.

Crude on purpose, same convention as tools/make_button_asset.py: small
deterministic boxes, not a reconstruction.
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
    make_bin_prop()


if __name__ == "__main__":
    main()
