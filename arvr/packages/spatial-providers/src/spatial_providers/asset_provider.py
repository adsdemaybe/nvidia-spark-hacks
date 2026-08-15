"""AssetProvider — Shadow Robot Spatial Demonstration Pipeline spec
section 13. Every source (fixture today; downloaded/PartNet-style/Objaverse-
style/reconstructed later) must normalize to the same `InteractableAsset`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ar_contracts import InteractableAsset


class AssetProvider(ABC):
    @abstractmethod
    def get_asset_bundle(self, asset_id: str) -> InteractableAsset: ...
