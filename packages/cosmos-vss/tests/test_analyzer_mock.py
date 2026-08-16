from __future__ import annotations

from pathlib import Path

from cosmos_vss.analyzer import MockAnalyzer, source_from_file
from cosmos_vss.artifacts import Provenance, sha256_file, write_artifact
from cosmos_vss.schemas import SemanticEpisode


def test_mock_analyzer_produces_valid_episode_offline(tmp_path: Path):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes, never decoded")

    episode = MockAnalyzer().analyze(source_from_file(video), episode_id="episode_demo")

    assert isinstance(episode, SemanticEpisode)
    assert episode.backend == "mock"
    assert episode.task_type
    assert any(o.role == "manipulated_object" for o in episode.objects)
    assert any(o.role == "target" for o in episode.objects)
    assert len(episode.timeline) >= 1
    assert episode.success_condition is not None


def test_mock_analyzer_end_to_end_input_to_artifact(tmp_path: Path):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake video bytes")
    artifact_dir = tmp_path / "artifacts" / "semantic"

    source = source_from_file(video)
    result = MockAnalyzer().analyze_full(source, episode_id="episode_demo")

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

    assert semantic_path.exists()
    reloaded = SemanticEpisode.model_validate_json(semantic_path.read_text(encoding="utf-8"))
    assert reloaded.episode_id == "episode_demo"
