"""SceneManifest -- the lightweight index an AR client loads.

USD is the authoritative simulation representation; GLB is the mobile
visualization representation (STRUCT_2.md 34). Mobile clients should never need
to render full USD, so the manifest carries both and lets each consumer take
the one it can use.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class VisualAsset(BaseModel):
    id: str
    glb: str


class SceneManifest(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scene_id: str
    canonical_usd: str | None = None
    visual_assets: list[VisualAsset] = Field(default_factory=list)

    def asset(self, asset_id: str) -> VisualAsset | None:
        """Resolve a visual asset by id. Clients index by id, not position."""
        return next((a for a in self.visual_assets if a.id == asset_id), None)
