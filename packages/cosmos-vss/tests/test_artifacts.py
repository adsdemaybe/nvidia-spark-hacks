from __future__ import annotations

import json
from pathlib import Path

from cosmos_vss.analyzer import MockAnalyzer, source_from_file
from cosmos_vss.artifacts import Provenance, load_artifact, sha256_bytes, sha256_file, write_artifact


def test_sha256_file_is_stable(tmp_path: Path):
    f = tmp_path / "demo.mp4"
    f.write_bytes(b"not really a video but deterministic bytes")
    assert sha256_file(f) == sha256_file(f)
    assert sha256_file(f) == sha256_bytes(f.read_bytes())


def test_write_artifact_creates_directory_and_files(tmp_path: Path):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    artifact_dir = tmp_path / "artifacts" / "semantic"

    result = MockAnalyzer().analyze_full(source_from_file(video), episode_id="episode_001")
    provenance = Provenance(
        backend=result.episode.backend,
        model=result.episode.model,
        source_sha256=sha256_file(video),
        source_name=result.episode.source_name,
        episode_id=result.episode.episode_id,
        prompt_version="robot_demo_v1",
        schema_version=result.episode.schema_version,
    )

    semantic_path = write_artifact(
        artifact_dir,
        result.episode,
        raw_response=result.raw_text,
        request_payload=result.request_payload,
        provenance=provenance,
    )

    out_dir = artifact_dir / result.episode.semantic_id
    assert out_dir.is_dir()
    assert semantic_path == out_dir / "semantic.json"
    for name in ("semantic.json", "raw_response.json", "request.json", "provenance.json"):
        assert (out_dir / name).exists()

    # semantic.json validates
    reloaded = load_artifact(artifact_dir, result.episode.semantic_id)
    assert reloaded == result.episode

    # raw response preserved verbatim
    raw = json.loads((out_dir / "raw_response.json").read_text(encoding="utf-8"))
    assert raw["raw_text"] == result.raw_text

    # provenance written with the source hash
    prov = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert prov["source_sha256"] == sha256_file(video)
    assert prov["episode_id"] == "episode_001"
    assert prov["backend"] == "mock"


def test_load_artifact_missing_raises_file_not_found(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_artifact(tmp_path / "artifacts", "sem_doesnotexist")
