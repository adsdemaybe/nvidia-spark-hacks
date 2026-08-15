#!/usr/bin/env python3
"""Generate the required fixture pack (spec section 57) deterministically.

    uv run python tools/make_fixtures.py

Regenerates everything under fixtures/ar-xr/ except the GLB binary assets
(table.glb, cube.glb, bin.glb, robot.glb), which are not fabricated here —
see fixtures/ar-xr/ASSETS_TODO.md. Nobody should wait on another feat to
start developing (spec section 57); this script is what makes that true for
the schema/motion side of the fixture pack.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from ar_contracts import (
    CorrectionEvent,
    EpisodeSource,
    ObjectState,
    Pose,
    RobotState,
    SceneManifest,
    Source,
    SpatialEpisode,
    SpatialEvent,
    SpatialFrame,
    Target,
    TaskState,
    TwinState,
    VisualAsset,
    compute_follow_target,
)
from ar_contracts.follow_state import FollowState

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ar-xr"
SEED = 1337
NS_PER_S = 1_000_000_000
BASE_TS_NS = 1_700_000_000 * NS_PER_S


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path.relative_to(FIXTURES_DIR.parent.parent)}")


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    print(f"wrote {path.relative_to(FIXTURES_DIR.parent.parent)} ({len(lines)} lines)")


def make_scene_manifest() -> None:
    manifest = SceneManifest(
        scene_id="demo_room",
        canonical_usd="artifacts/demo_room/scene.usd",
        visual_assets=(
            VisualAsset(id="table", glb="table.glb"),
            VisualAsset(id="cube", glb="cube.glb"),
            VisualAsset(id="bin", glb="bin.glb"),
            VisualAsset(id="robot", glb="robot.glb"),
        ),
    )
    write_json(FIXTURES_DIR / "scene.json", json.loads(manifest.model_dump_json()))


def make_fake_twin_state(n_frames: int = 60, hz: float = 30.0) -> None:
    lines = []
    for i in range(n_frames):
        t = i / hz
        ts = BASE_TS_NS + int(t * NS_PER_S)
        joints = tuple(round(0.3 * math.sin(t * 0.5 + j), 6) for j in range(6))
        cube_x = round(0.3 + 0.05 * math.sin(t * 0.3), 6)
        state = TwinState(
            timestamp_ns=ts,
            scene_id="demo_room",
            robot=RobotState(id="robot_01", joint_positions=joints),
            objects=(
                ObjectState(
                    id="cube_01",
                    position_m=(cube_x, 0.1, 0.7),
                    orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
                ),
            ),
            task=TaskState(id="cube_to_bin", status="running"),
        )
        lines.append(json.loads(state.model_dump_json()))
    write_jsonl(FIXTURES_DIR / "fake_twin_state.jsonl", lines)


def make_sample_follow(n_frames: int = 40, hz: float = 10.0) -> None:
    lines = []
    distance = 1.5
    for i in range(n_frames):
        t = i / hz
        ts = BASE_TS_NS + int(t * NS_PER_S)
        # Person walks a slow arc around the table, always facing +X (identity
        # orientation) so the follow target trails directly behind them.
        pos = (round(1.0 + 0.3 * math.sin(t * 0.4), 6), round(2.0 - 0.1 * t, 6), 0.0)
        orientation = (0.0, 0.0, 0.0, 1.0)
        target = compute_follow_target(pos, orientation, distance)
        state = FollowState(
            timestamp_ns=ts,
            human_pose=Pose(position_m=pos, orientation_xyzw=orientation),
            desired_follow_distance_m=distance,
            follow_target=Target(position_m=tuple(round(c, 6) for c in target)),
            state="following",
        )
        lines.append(json.loads(state.model_dump_json()))
    write_jsonl(FIXTURES_DIR / "sample_follow.jsonl", lines)


def make_sample_correction() -> None:
    correction = CorrectionEvent(
        task_id="cube_to_bin",
        timestamp_ns=BASE_TS_NS,
        original_target=Target(position_m=(0.4, 0.2, 0.5)),
        corrected_target=Target(position_m=(0.45, 0.25, 0.58)),
        reason="collision_avoidance",
    )
    write_json(
        FIXTURES_DIR / "sample_correction.json", json.loads(correction.model_dump_json())
    )


def make_sample_episode(hz: float = 30.0) -> None:
    """A deterministic TEACH demo (spec section 74): approach -> GRAB -> lift
    -> move -> RELEASE. Frames are written as JSONL (`sample_episode.jsonl`)
    rather than Parquet — pyarrow is not a dependency of this fixture-only
    script; a real recording pipeline (Phase 4) will write Parquet per spec
    section 35 using r2s-core's existing Parquet path as a reference."""
    rng = random.Random(SEED)
    episode_id = "5b1f7b0a-6b8b-4a3a-9c1a-7a2f6b0c9d1e"

    waypoints = [
        # (duration_s, start_xyz, end_xyz, gripper)
        (1.0, (0.10, -0.20, 0.50), (0.30, 0.10, 0.55), 0.0),  # approach
        (0.3, (0.30, 0.10, 0.55), (0.30, 0.10, 0.50), 0.0),   # descend to grab
        (0.6, (0.30, 0.10, 0.50), (0.30, 0.10, 0.50), 1.0),   # GRAB (hold)
        (1.2, (0.30, 0.10, 0.50), (0.60, 0.30, 0.55), 1.0),   # move to bin
        (0.4, (0.60, 0.30, 0.55), (0.60, 0.30, 0.55), 0.0),   # RELEASE (hold)
    ]

    frames = []
    events = []
    t = 0.0
    orientation = (0.0, 0.0, 0.0, 1.0)
    for idx, (duration, start, end, gripper) in enumerate(waypoints):
        n = max(2, int(duration * hz))
        for k in range(n):
            frac = k / (n - 1)
            pos = tuple(
                round(s + (e - s) * frac + rng.uniform(-0.0015, 0.0015), 6)
                for s, e in zip(start, end, strict=True)
            )
            ts = BASE_TS_NS + int(t * NS_PER_S)
            frame = SpatialFrame(
                timestamp_ns=ts,
                source=Source(device_type="phone", input_type="tracked_controller"),
                frame="struct_world",
                position_m=pos,
                orientation_xyzw=orientation,
                gripper=gripper,
            )
            frames.append(json.loads(frame.model_dump_json()))
            t += 1.0 / hz
        if idx == 1:
            events.append(
                SpatialEvent(type="GRAB", timestamp_ns=BASE_TS_NS + int(t * NS_PER_S))
            )
        if idx == 3:
            events.append(
                SpatialEvent(type="RELEASE", timestamp_ns=BASE_TS_NS + int(t * NS_PER_S))
            )

    write_jsonl(FIXTURES_DIR / "sample_episode.jsonl", frames)

    episode = SpatialEpisode(
        episode_id=episode_id,
        task_id="cube_to_bin",
        source=EpisodeSource(device_type="phone"),
        coordinate_frame="struct_world",
        frames_artifact="sample_episode.jsonl",
        events=tuple(events),
    )
    write_json(FIXTURES_DIR / "sample_episode.json", json.loads(episode.model_dump_json()))


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    make_scene_manifest()
    make_fake_twin_state()
    make_sample_follow()
    make_sample_correction()
    make_sample_episode()


if __name__ == "__main__":
    main()
