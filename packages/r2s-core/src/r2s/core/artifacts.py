"""Artifact envelope: the header/payload split every stage produces.

Two properties matter and are easy to lose:

1. `artifact_id` hashes ONLY the payload. So a deterministic stage re-run on a
   different machine with the same inputs produces the same id, and a GPU stage
   produces a different one. That difference is diagnostic information, not a
   bug -- do not "fix" it by hashing the header too.

2. Degradation is monotone downstream. An artifact's level is the max of its
   inputs' levels and whatever it degraded on itself. There is no code path that
   lowers it. This is what lets a weak segmentation still ship 8 labeled scenes
   without anyone later mistaking them for clean ones.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import IntEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class DegradationLevel(IntEnum):
    FULL = 0
    DEGRADED = 1
    STUB = 2


class Degradation(BaseModel):
    """A named, machine-readable reason an artifact is not FULL."""

    model_config = ConfigDict(frozen=True)

    code: str  # "SEG.NO_MASKS" | "RECON.SCALE_ASSUMED" | "ASSET.HULL_FALLBACK"
    level: DegradationLevel
    stage: str
    message: str
    remediation: str | None = None


class ArtifactRef(BaseModel):
    """A pointer into the content-addressed store."""

    model_config = ConfigDict(frozen=True)

    uri: str  # "cas://sha256/<hex>"
    sha256: str
    size_bytes: int
    media_type: str  # "application/json" | "model/vnd.usdz+zip" | "application/x-ply"


class CodeVersion(BaseModel):
    git_sha: str | None = None
    dirty: bool = False
    packages: dict[str, str] = Field(default_factory=dict)


def canonical_json(obj) -> bytes:
    """Stable bytes for hashing: sorted keys, no whitespace, no NaN."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


class ArtifactHeader(BaseModel):
    schema_version: str  # semver PER artifact type
    artifact_type: str
    artifact_id: str  # sha256 of the canonical payload
    run_id: str
    stage: str
    backend: str  # "3dgrut" | "gsplat" | "torch_ref" | "stub" -- which impl produced this
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input_digest: str  # THE cache key
    input_refs: list[ArtifactRef] = Field(default_factory=list)
    code_version: CodeVersion = Field(default_factory=CodeVersion)
    seed: int = 0
    degradation: DegradationLevel = DegradationLevel.FULL
    degradations: list[Degradation] = Field(default_factory=list)
    gate_report: ArtifactRef | None = None
    wallclock_s: float | None = None


P = TypeVar("P", bound=BaseModel)


class BaseArtifact(BaseModel, Generic[P]):
    header: ArtifactHeader
    payload: P

    @property
    def degradation(self) -> DegradationLevel:
        return self.header.degradation

    def worst_degradation(self) -> DegradationLevel:
        """Max of the header level and every recorded degradation record."""
        levels = [self.header.degradation, *(d.level for d in self.header.degradations)]
        return DegradationLevel(max(levels))


def inherit_degradation(
    inputs: list[BaseArtifact], own: list[Degradation] | None = None
) -> tuple[DegradationLevel, list[Degradation]]:
    """Degradation is monotone: an artifact can never be cleaner than its inputs.

    Returns the level to stamp and the full accumulated record list. Upstream
    degradations are carried forward verbatim so a STUB reconstruction is still
    visible in the scene artifact eight stages later.
    """
    own = own or []
    carried: list[Degradation] = []
    for art in inputs:
        carried.extend(art.header.degradations)
    all_records = [*carried, *own]
    level = DegradationLevel.FULL
    for art in inputs:
        level = DegradationLevel(max(level, art.worst_degradation()))
    for d in own:
        level = DegradationLevel(max(level, d.level))
    return level, all_records
