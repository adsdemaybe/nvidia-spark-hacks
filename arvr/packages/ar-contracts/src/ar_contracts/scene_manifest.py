"""SceneManifest — spec section 34.

The lightweight manifest AR clients load. USD stays authoritative for
simulation; GLB is the mobile visualization representation (spec rule:
"USD = authoritative simulation representation, GLB = lightweight AR
visualization representation").
"""

from __future__ import annotations

from .common import SCHEMA_VERSION, FrozenModel, SchemaVersion


class VisualAsset(FrozenModel):
    id: str
    glb: str


class SceneManifest(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    scene_id: str
    canonical_usd: str
    visual_assets: tuple[VisualAsset, ...] = ()
