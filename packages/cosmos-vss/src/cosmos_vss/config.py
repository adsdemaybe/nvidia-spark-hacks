"""Runtime configuration, read from the environment and nowhere else.

Model/backend selection must stay out of business logic (COSMOS_VSS.md
§16) — everything downstream reads `Config`, never `os.environ` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_VALID_BACKENDS = ("vss", "cosmos", "mock")


@dataclass(frozen=True)
class Config:
    backend: str
    vss_base_url: str
    vss_model: str
    cosmos_base_url: str
    cosmos_model: str
    artifact_dir: Path
    timeout_s: float

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = env if env is not None else os.environ
        backend = e.get("COSMOS_VSS_BACKEND", "vss")
        if backend not in _VALID_BACKENDS:
            raise ValueError(f"unknown COSMOS_VSS_BACKEND: {backend!r} (expected one of {_VALID_BACKENDS})")

        try:
            timeout_s = float(e.get("COSMOS_VSS_TIMEOUT_S", "180"))
        except ValueError as exc:
            raise ValueError(f"invalid COSMOS_VSS_TIMEOUT_S: {e.get('COSMOS_VSS_TIMEOUT_S')!r}") from exc

        return cls(
            backend=backend,
            vss_base_url=e.get("VSS_BASE_URL", "http://127.0.0.1:8000/v1"),
            vss_model=e.get("VSS_MODEL", "cosmos-reason2"),
            cosmos_base_url=e.get("COSMOS_BASE_URL", "http://127.0.0.1:8000/v1"),
            cosmos_model=e.get("COSMOS_MODEL", "nvidia/cosmos-reason2-8b"),
            artifact_dir=Path(e.get("COSMOS_VSS_ARTIFACT_DIR", "artifacts/semantic")),
            timeout_s=timeout_s,
        )

    @property
    def active_model(self) -> str:
        if self.backend == "cosmos":
            return self.cosmos_model
        if self.backend == "mock":
            return "mock-cosmos-reason"
        return self.vss_model

    @property
    def active_base_url(self) -> str:
        return self.cosmos_base_url if self.backend == "cosmos" else self.vss_base_url
