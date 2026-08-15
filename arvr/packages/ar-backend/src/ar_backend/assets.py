"""Assets API — Shadow Robot Spatial Demonstration Pipeline spec section 46.

    GET /assets               enumerate available fixture assets
    GET /assets/{asset_id}    one asset's manifest

Same file/JSON-only, cross-platform pattern as robots.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

DEFAULT_ASSETS_DIR = (
    Path(__file__).resolve().parents[4] / "fixtures" / "spatial-training" / "assets"
)


def build_router(assets_dir: Path | None = None) -> APIRouter:
    assets_dir = assets_dir or DEFAULT_ASSETS_DIR
    router = APIRouter(prefix="/assets", tags=["assets"])

    @router.get("")
    def list_assets() -> list[dict]:
        manifests = []
        if assets_dir.exists():
            for manifest_path in sorted(assets_dir.glob("*/manifest.json")):
                manifests.append(json.loads(manifest_path.read_text()))
        return manifests

    @router.get("/{asset_id}")
    def get_asset(asset_id: str) -> dict:
        # asset_id ("button_01") -> fixture dir ("button") -- the numeric
        # suffix distinguishes instances at recording time, not different
        # fixture bundles on disk. Same convention as
        # spatial_providers.FixtureAssetProvider._bundle_dir; keep in sync.
        bundle_dir_name = asset_id.rsplit("_", 1)[0]
        manifest_path = assets_dir / bundle_dir_name / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(404, f"unknown asset_id {asset_id!r}")
        return json.loads(manifest_path.read_text())

    return router
