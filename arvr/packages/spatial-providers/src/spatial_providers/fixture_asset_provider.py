"""FixtureAssetProvider — button/cube/bin/drawer, starting with button
(spec section 14: "do not start with the hardest interaction"; button is
first in the recommended demo progression).
"""

from __future__ import annotations

import json
from pathlib import Path

from ar_contracts import InteractableAsset

from .asset_provider import AssetProvider

ARVR_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSETS_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "assets"


class UnknownAssetError(ValueError):
    pass


class FixtureAssetProvider(AssetProvider):
    def __init__(self, assets_dir: Path | None = None) -> None:
        self.assets_dir = assets_dir or DEFAULT_ASSETS_DIR

    def get_asset_bundle(self, asset_id: str) -> InteractableAsset:
        bundle_dir = self._bundle_dir(asset_id)
        manifest = json.loads((bundle_dir / "manifest.json").read_text())
        interaction_path = bundle_dir / manifest["interaction"]
        return InteractableAsset.model_validate(json.loads(interaction_path.read_text()))

    def asset_glb_path(self, asset_id: str) -> Path:
        bundle_dir = self._bundle_dir(asset_id)
        manifest = json.loads((bundle_dir / "manifest.json").read_text())
        return (bundle_dir / manifest["asset_glb"]).resolve()

    def _bundle_dir(self, asset_id: str) -> Path:
        # asset_id "button_01" -> fixture dir "button" (the numeric suffix
        # distinguishes instances of the same asset type at recording time,
        # not different fixture bundles on disk).
        bundle_dir = self.assets_dir / asset_id.rsplit("_", 1)[0]
        if not (bundle_dir / "manifest.json").exists():
            raise UnknownAssetError(f"no fixture asset bundle for {asset_id!r} at {bundle_dir}")
        return bundle_dir
