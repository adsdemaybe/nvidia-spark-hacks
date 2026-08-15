#!/usr/bin/env python3
"""Generate the fixture button InteractableAsset deterministically (Shadow
Robot Spatial Demonstration Pipeline spec section 15).

    uv run python tools/make_button_asset.py

Writes fixtures/spatial-training/assets/button/{manifest,interaction}.json
and asset.glb. Cube/bin/drawer are explicitly deferred (spec section 14
recommends button as the first demo task) -- only button is needed for
Milestone 1.

Crude on purpose, same convention as tools/make_assets.py: a small
deterministic cylinder, not a reconstruction. interaction.json's numbers are
spec section 15's own literal button example.
"""

from __future__ import annotations

import json
from pathlib import Path

import trimesh

ARVR_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "assets" / "button"

BUTTON_RADIUS = 0.015
BUTTON_HEIGHT = 0.02


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path.relative_to(ARVR_ROOT)}")


def make_manifest() -> None:
    write_json(
        BUNDLE_DIR / "manifest.json",
        {
            "schema_version": "1.0",
            "asset_id": "button_01",
            "asset_glb": "asset.glb",
            "interaction": "interaction.json",
        },
    )


def make_interaction() -> None:
    # Spec section 15's literal button example.
    write_json(
        BUNDLE_DIR / "interaction.json",
        {
            "schema_version": "1.0",
            "asset_id": "button_01",
            "parts": {
                "button": {
                    "interaction": "press",
                    "local_origin_m": [0.0, 0.0, 0.02],
                    "axis": [0.0, 0.0, -1.0],
                    "travel_m": 0.006,
                }
            },
        },
    )


def make_glb() -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.cylinder(radius=BUTTON_RADIUS, height=BUTTON_HEIGHT, sections=24)
    mesh.apply_translation((0.0, 0.0, BUTTON_HEIGHT / 2.0))
    glb = trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh), include_normals=True)
    path = BUNDLE_DIR / "asset.glb"
    path.write_bytes(glb)
    print(f"wrote {path.relative_to(ARVR_ROOT)} ({path.stat().st_size} bytes)")


def main() -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    make_manifest()
    make_interaction()
    make_glb()


if __name__ == "__main__":
    main()
