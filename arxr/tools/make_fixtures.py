"""Generate the AR/XR fixture pack (STRUCT_2.md 57).

    uv run python tools/make_fixtures.py

"Nobody should wait for another feat before developing." This pack is what
PLACE, TEACH, REPLAY, FOLLOW, TWIN and CORRECT are built against until real
scene artifacts and the Isaac bridge exist.

Everything here is deterministic -- fixed seed, fixed epoch, no wall clock --
so regenerating produces byte-identical files and a fixture diff always means
somebody changed something on purpose.

The geometry is deliberately crude. These are stand-ins with correct units and
plausible extents, not a reconstruction; the real assets arrive from F3 as
scene.json plus GLBs (STRUCT_2.md 84).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import trimesh
from arxr.core.follow import FollowSession
from arxr.core.schemas import (
    CorrectionEvent,
    EpisodeEvent,
    Pose,
    SceneManifest,
    SpatialEpisode,
    VisualAsset,
)
from arxr.core.twin_mock import EPOCH_NS, MockTwinSource

SCENE_ID = "demo_room"
TASK_ID = "cube_to_bin"
HZ = 30.0
TWIN_FRAMES = 90  # 3 seconds
EPISODE_FRAMES = 180  # 6 seconds of demonstration
FOLLOW_FRAMES = 60

# Meters. A waist-height table, a graspable cube, a bin beside it.
TABLE_SIZE = (1.20, 0.80, 0.75)
CUBE_SIZE = 0.06
BIN_SIZE = (0.30, 0.30, 0.25)
ROBOT_BASE_RADIUS = 0.15
ROBOT_HEIGHT = 0.90


def _write_glb(mesh: trimesh.Trimesh, path: Path) -> None:
    """Export with metadata stripped, so the bytes depend only on geometry.

    trimesh stamps a generator string and (in some versions) a timestamp into
    the GLB asset block; leaving those in would break the determinism gate.
    """
    scene = trimesh.Scene(mesh)
    glb = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
    path.write_bytes(glb)


def _table() -> trimesh.Trimesh:
    """Box sitting on the floor, origin at its centre in x/y and z=0 underneath."""
    mesh = trimesh.creation.box(extents=TABLE_SIZE)
    mesh.apply_translation((0.0, 0.0, TABLE_SIZE[2] / 2.0))
    return mesh


def _cube() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE))


def _bin() -> trimesh.Trimesh:
    """Open-topped box, built from a floor and four walls.

    Assembled rather than CSG-differenced on purpose: a boolean would pull in
    manifold3d/blender as a build dependency, and the result would vary with
    whichever engine happened to be installed -- which breaks determinism.
    """
    w, d, h = BIN_SIZE
    t = 0.02  # wall thickness

    parts = []
    floor = trimesh.creation.box(extents=(w, d, t))
    floor.apply_translation((0.0, 0.0, t / 2.0))
    parts.append(floor)

    for dx, dy, extents in (
        ((w - t) / 2.0, 0.0, (t, d, h)),
        (-(w - t) / 2.0, 0.0, (t, d, h)),
        (0.0, (d - t) / 2.0, (w, t, h)),
        (0.0, -(d - t) / 2.0, (w, t, h)),
    ):
        wall = trimesh.creation.box(extents=extents)
        wall.apply_translation((dx, dy, h / 2.0))
        parts.append(wall)

    return trimesh.util.concatenate(parts)


def _robot() -> trimesh.Trimesh:
    """A cylinder base and a column. Stands in for the arm's footprint and
    reach envelope so PLACE has something with real extents to position."""
    base = trimesh.creation.cylinder(radius=ROBOT_BASE_RADIUS, height=0.10, sections=24)
    base.apply_translation((0.0, 0.0, 0.05))
    column = trimesh.creation.cylinder(radius=0.05, height=ROBOT_HEIGHT, sections=16)
    column.apply_translation((0.0, 0.0, 0.10 + ROBOT_HEIGHT / 2.0))
    return trimesh.util.concatenate([base, column])


ASSETS = {
    "table": _table,
    "cube": _cube,
    "bin": _bin,
    "robot": _robot,
}


def _demonstration_pose(
    i: int, total: int
) -> tuple[tuple[float, ...], tuple[float, ...], float]:
    """A reach-grasp-lift-move-release arc for the phone-as-end-effector.

    Deterministic and smooth; TEACH's recorder and the retargeting stage both
    need a trajectory with no discontinuities to be a fair test (STRUCT_2.md 62).
    """
    t = i / (total - 1)

    # Approach the cube on the table, lift, traverse to the bin, lower.
    start = np.array([0.10, 0.00, 0.90])
    grasp = np.array([0.30, 0.10, 0.78])
    lifted = np.array([0.30, 0.10, 1.00])
    over_bin = np.array([0.60, -0.70, 1.00])
    release = np.array([0.60, -0.70, 0.35])

    if t < 0.25:
        p = start + (grasp - start) * (t / 0.25)
    elif t < 0.40:
        p = grasp + (lifted - grasp) * ((t - 0.25) / 0.15)
    elif t < 0.75:
        p = lifted + (over_bin - lifted) * ((t - 0.40) / 0.35)
    else:
        p = over_bin + (release - over_bin) * ((t - 0.75) / 0.25)

    # Gripper closes at the grasp and opens at the release.
    gripper = 1.0 if 0.25 <= t < 0.90 else 0.0

    # Slow roll about Z so orientation is not trivially constant.
    yaw = math.radians(30.0) * math.sin(t * math.pi)
    quat = (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))

    return (tuple(float(v) for v in p), quat, gripper)


def build_fixtures(dest: Path) -> Path:
    """Write the whole pack into `dest`. Returns `dest`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # ---- visual assets -----------------------------------------------------
    for name, build in ASSETS.items():
        _write_glb(build(), dest / f"{name}.glb")

    # ---- scene manifest ----------------------------------------------------
    manifest = SceneManifest(
        scene_id=SCENE_ID,
        canonical_usd=None,  # no USD until F3 hands one over (STRUCT_2.md 84)
        visual_assets=[VisualAsset(id=n, glb=f"{n}.glb") for n in ASSETS],
    )
    (dest / "scene.json").write_text(manifest.model_dump_json(indent=2) + "\n")

    # ---- fake twin stream --------------------------------------------------
    source = MockTwinSource(scene_id=SCENE_ID, hz=HZ)
    twin_lines = [source.at_tick(i).model_dump_json() for i in range(TWIN_FRAMES)]
    (dest / "fake_twin_state.jsonl").write_text("\n".join(twin_lines) + "\n")

    # ---- sample follow stream ----------------------------------------------
    session = FollowSession()
    session.start()
    follow_lines = []
    for i in range(FOLLOW_FRAMES):
        t = i / HZ
        # Human walks a gentle arc around the table.
        human = Pose(
            position_m=(1.5 * math.cos(t * 0.5), 1.5 * math.sin(t * 0.5), 0.0),
            orientation_xyzw=(0.0, 0.0, math.sin(t * 0.25), math.cos(t * 0.25)),
        )
        state = session.update(human, timestamp_ns=EPOCH_NS + round(i * 1e9 / HZ))
        follow_lines.append(state.model_dump_json())
    (dest / "sample_follow.jsonl").write_text("\n".join(follow_lines) + "\n")

    # ---- sample episode: parquet poses + json metadata ---------------------
    stamps, positions, orientations, grippers = [], [], [], []
    for i in range(EPISODE_FRAMES):
        position, quat, gripper = _demonstration_pose(i, EPISODE_FRAMES)
        stamps.append(EPOCH_NS + round(i * 1e9 / HZ))
        positions.append(list(position))
        orientations.append(list(quat))
        grippers.append(gripper)

    table = pa.table(
        {
            "timestamp_ns": pa.array(stamps, type=pa.int64()),
            "position_m": pa.array(positions, type=pa.list_(pa.float64(), 3)),
            "orientation_xyzw": pa.array(orientations, type=pa.list_(pa.float64(), 4)),
            "gripper": pa.array(grippers, type=pa.float64()),
        }
    )
    pq.write_table(table, dest / "sample_episode.parquet", compression="none",
                   write_statistics=False, store_schema=False)

    grab_at = EPOCH_NS + round(EPISODE_FRAMES * 0.25 * 1e9 / HZ)
    release_at = EPOCH_NS + round(EPISODE_FRAMES * 0.90 * 1e9 / HZ)
    episode = SpatialEpisode(
        episode_id="3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        task_id=TASK_ID,
        source={"device_type": "phone"},
        coordinate_frame="struct_world",
        frames_artifact="sample_episode.parquet",
        events=[
            EpisodeEvent(type="START", timestamp_ns=stamps[0]),
            EpisodeEvent(type="GRAB", timestamp_ns=grab_at),
            EpisodeEvent(type="RELEASE", timestamp_ns=release_at),
            EpisodeEvent(type="FINISH", timestamp_ns=stamps[-1]),
        ],
    )
    (dest / "sample_episode.json").write_text(episode.model_dump_json(indent=2) + "\n")

    # ---- sample correction -------------------------------------------------
    correction = CorrectionEvent(
        task_id=TASK_ID,
        timestamp_ns=EPOCH_NS + round(EPISODE_FRAMES * 0.55 * 1e9 / HZ),
        original_target=Pose(position_m=(0.45, -0.05, 1.00)),
        corrected_target=Pose(position_m=(0.45, -0.05, 1.18)),
        reason="collision_avoidance",
    )
    (dest / "sample_correction.json").write_text(correction.model_dump_json(indent=2) + "\n")

    return dest


DEFAULT_DEST = Path(__file__).resolve().parents[1] / "fixtures" / "ar-xr"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args()

    dest = build_fixtures(args.dest)
    written = sorted(p.name for p in dest.iterdir())
    print(f"wrote {len(written)} files to {dest}")
    for name in written:
        print(f"  {name}  ({(dest / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
