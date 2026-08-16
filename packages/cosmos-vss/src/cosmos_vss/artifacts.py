"""Semantic artifact writer.

Every analysis writes four files under `artifact_dir/<semantic_id>/`:
semantic.json (the validated `SemanticEpisode`), raw_response.json (the
untouched model text, for debugging when parsing/validation fails),
request.json (what was sent), and provenance.json (backend/model/source hash
so a later regeneration with a different model or prompt can be told apart).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .schemas import SemanticEpisode


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Provenance:
    backend: str
    model: str
    source_sha256: str
    source_name: str
    episode_id: str | None
    prompt_version: str
    schema_version: str

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "source_sha256": self.source_sha256,
            "source_name": self.source_name,
            "episode_id": self.episode_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
        }


def write_artifact(
    artifact_dir: Path,
    episode: SemanticEpisode,
    *,
    raw_response: str,
    request_payload: dict,
    provenance: Provenance,
) -> Path:
    out_dir = artifact_dir / episode.semantic_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "semantic.json").write_text(episode.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "raw_response.json").write_text(
        json.dumps({"raw_text": raw_response}, indent=2), encoding="utf-8"
    )
    (out_dir / "request.json").write_text(json.dumps(request_payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "provenance.json").write_text(json.dumps(provenance.to_dict(), indent=2), encoding="utf-8")

    return out_dir / "semantic.json"


def load_artifact(artifact_dir: Path, semantic_id: str) -> SemanticEpisode:
    path = artifact_dir / semantic_id / "semantic.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return SemanticEpisode.model_validate_json(path.read_text(encoding="utf-8"))
