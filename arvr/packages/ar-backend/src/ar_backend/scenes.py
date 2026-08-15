"""Scene API — spec section 37.

    GET /scenes/{scene_id}

Serves whatever `SceneManifest` JSON files exist in `scenes_dir`, indexed
by each file's own `scene_id` field (not its filename) — points at
`fixtures/ar-xr/` by default, so `GET /scenes/demo_room` works against the
committed fixture pack with zero extra setup.
"""

from __future__ import annotations

import json
from pathlib import Path

from ar_contracts import SceneManifest
from fastapi import APIRouter, HTTPException


def _load_manifests(scenes_dir: Path) -> dict[str, SceneManifest]:
    manifests: dict[str, SceneManifest] = {}
    for path in scenes_dir.glob("*.json"):
        try:
            manifest = SceneManifest.model_validate(json.loads(path.read_text()))
        except Exception:  # noqa: BLE001 — not every *.json here is a SceneManifest
            continue
        manifests[manifest.scene_id] = manifest
    return manifests


def build_router(scenes_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/scenes", tags=["scenes"])

    @router.get("/{scene_id}", response_model=SceneManifest)
    def get_scene(scene_id: str) -> SceneManifest:
        manifests = _load_manifests(scenes_dir)
        manifest = manifests.get(scene_id)
        if manifest is None:
            raise HTTPException(404, f"unknown scene_id {scene_id!r}")
        return manifest

    return router
