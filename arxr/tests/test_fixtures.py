"""Acceptance gate for the fixture pack (STRUCT_2.md 57).

"Nobody should wait for another feat before developing." Every mode Andrew owns
builds against these, so the pack has to be self-consistent: the manifest must
reference assets that exist, and every sample must validate against the frozen
contract it claims to be.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from arxr.core.schemas import (
    CorrectionEvent,
    FollowState,
    SceneManifest,
    SpatialEpisode,
    TwinState,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from make_fixtures import build_fixtures  # noqa: E402


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("ar-xr")
    build_fixtures(dest)
    return dest


def test_pack_contains_every_file_the_spec_lists(pack: Path):
    expected = {
        "scene.json",
        "table.glb",
        "cube.glb",
        "bin.glb",
        "robot.glb",
        "fake_twin_state.jsonl",
        "sample_episode.parquet",
        "sample_follow.jsonl",
        "sample_correction.json",
    }

    assert {p.name for p in pack.iterdir()} >= expected


def test_scene_manifest_validates_and_its_assets_exist(pack: Path):
    """A manifest pointing at a missing GLB fails at runtime on the device,
    where it is expensive to debug. Catch it here."""
    manifest = SceneManifest.model_validate_json((pack / "scene.json").read_text())

    assert manifest.scene_id == "demo_room"
    for asset in manifest.visual_assets:
        assert (pack / Path(asset.glb).name).exists(), f"{asset.id} -> {asset.glb} missing"


def test_every_twin_state_line_validates(pack: Path):
    lines = (pack / "fake_twin_state.jsonl").read_text().splitlines()

    states = [TwinState.model_validate_json(line) for line in lines if line.strip()]

    assert len(states) >= 30
    assert all(s.scene_id == "demo_room" for s in states)


def test_twin_state_timestamps_increase(pack: Path):
    lines = (pack / "fake_twin_state.jsonl").read_text().splitlines()
    states = [TwinState.model_validate_json(line) for line in lines if line.strip()]

    stamps = [s.timestamp_ns for s in states]
    assert stamps == sorted(stamps)


def test_every_follow_line_validates(pack: Path):
    lines = (pack / "sample_follow.jsonl").read_text().splitlines()

    states = [FollowState.model_validate_json(line) for line in lines if line.strip()]

    assert len(states) >= 30


def test_sample_correction_validates(pack: Path):
    event = CorrectionEvent.model_validate_json((pack / "sample_correction.json").read_text())

    assert event.original_target != event.corrected_target


def test_sample_episode_parquet_round_trips_to_spatial_frames(pack: Path):
    """Metadata is JSON, the high-rate pose stream is Parquet (STRUCT_2.md 35).
    The two have to line up or the demonstration is unusable."""
    import pyarrow.parquet as pq

    table = pq.read_table(pack / "sample_episode.parquet")
    rows = table.to_pylist()

    assert len(rows) >= 100
    assert [r["timestamp_ns"] for r in rows] == sorted(r["timestamp_ns"] for r in rows)
    assert {"timestamp_ns", "position_m", "orientation_xyzw", "gripper"} <= set(
        table.column_names
    )


def test_episode_metadata_matches_its_parquet_artifact(pack: Path):
    meta = json.loads((pack / "sample_episode.json").read_text())
    episode = SpatialEpisode.model_validate(meta)

    assert (pack / episode.frames_artifact).exists()
    assert [e.type for e in episode.events] == ["START", "GRAB", "RELEASE", "FINISH"]


def test_generation_is_deterministic(tmp_path: Path):
    """Two runs must be byte-identical, or fixture churn shows up as noise in
    every diff and nobody can tell a real change from a regenerated one."""
    a, b = tmp_path / "a", tmp_path / "b"
    build_fixtures(a)
    build_fixtures(b)

    for path in sorted(a.iterdir()):
        assert path.read_bytes() == (b / path.name).read_bytes(), f"{path.name} differs"
