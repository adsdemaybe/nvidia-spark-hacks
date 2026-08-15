"""HandProvider — Shadow Robot Spatial Demonstration Pipeline spec section
16. OpenXR (best full-spatial capture) > phone (fallback) > mock (automated
development) is the priority order; only MockHandProvider is implemented in
this package -- OpenXR/phone hand tracking happens client-side in xr-web
(hands.ts), not through this Python interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ar_contracts import HandFrame


class HandProvider(ABC):
    @abstractmethod
    def stream(self) -> Iterator[HandFrame]: ...
