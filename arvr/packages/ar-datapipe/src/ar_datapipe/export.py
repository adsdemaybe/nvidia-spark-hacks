"""LeRobot-compatible dataset export — spec section 13E / 35.

"AR/XR does not perform model training. It guarantees compatible output."

This writes the LeRobot v3 on-disk layout (data/chunk-000/episode_*.parquet
+ meta/{info,episodes,tasks}.jsonl) closely enough to be usable, but it has
NOT been round-tripped through the actual `lerobot` python package — that
package pulls in torch/gymnasium/etc, a much heavier dependency than this
export step needs. Before trusting this for real training data, load one
written dataset with `lerobot.datasets.lerobot_dataset.LeRobotDataset` and
confirm it opens; that check is not part of this module yet (flagged in
STATE.md).
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised via pytest.importorskip
    # See retarget.py's matching comment: deferred so `import ar_datapipe`
    # (and anything transitively importing it) still succeeds on Windows.
    pa = None
    pq = None

LEROBOT_CODEBASE_VERSION = "v3.0"


def export_episode(
    dataset_dir: Path,
    *,
    episode_index: int,
    task: str,
    robot_type: str,
    fps: float,
    timestamps_s: list[float],
    actions: list[tuple[float, ...]],
    gripper: list[float],
) -> str:
    """Writes one episode into `dataset_dir` in LeRobot v3 layout. Returns a
    dataset_id string suitable for `VerificationResult.dataset_id`."""
    if pa is None or pq is None:
        raise ImportError(
            "pyarrow is not installed on this platform (Linux only — see "
            "packages/ar-datapipe/README.md). ar_datapipe itself imports "
            "fine everywhere; only export_episode needs it."
        )
    if not (len(timestamps_s) == len(actions) == len(gripper)):
        raise ValueError("timestamps_s, actions, and gripper must be the same length")
    if not actions:
        raise ValueError("cannot export an empty episode")

    action_dim = len(actions[0])
    if any(len(a) != action_dim for a in actions):
        raise ValueError("all action vectors must have the same dimensionality")

    data_dir = dataset_dir / "data" / "chunk-000"
    meta_dir = dataset_dir / "meta"
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    table = pa.table(
        {
            "frame_index": pa.array(range(len(timestamps_s)), type=pa.int64()),
            "timestamp": pa.array(timestamps_s, type=pa.float64()),
            "action": pa.array(actions, type=pa.list_(pa.float32(), action_dim)),
            "gripper": pa.array(gripper, type=pa.float32()),
            "episode_index": pa.array(
                [episode_index] * len(timestamps_s), type=pa.int64()
            ),
        }
    )
    episode_path = data_dir / f"episode_{episode_index:06d}.parquet"
    pq.write_table(table, episode_path)

    info_path = meta_dir / "info.json"
    info = {
        "codebase_version": LEROBOT_CODEBASE_VERSION,
        "robot_type": robot_type,
        "fps": fps,
        "features": {
            "action": {"dtype": "float32", "shape": [action_dim]},
            "gripper": {"dtype": "float32", "shape": [1]},
        },
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n")

    tasks_path = meta_dir / "tasks.jsonl"
    with tasks_path.open("a") as f:
        f.write(json.dumps({"task_index": 0, "task": task}) + "\n")

    episodes_path = meta_dir / "episodes.jsonl"
    with episodes_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "episode_index": episode_index,
                    "tasks": [task],
                    "length": len(timestamps_s),
                }
            )
            + "\n"
        )

    return f"{dataset_dir.name}/episode_{episode_index:06d}"


def export_robot_episode(
    dataset_dir: Path,
    *,
    episode_index: int,
    task: str,
    robot_type: str,
    fps: float,
    timestamps_s: list[float],
    actions: list[tuple[float, ...]],
    gripper: list[float],
    metadata: object,  # ar_contracts.RobotEpisodeMetadata, typed loosely to
    # avoid an ar_contracts import here purely for a type hint -- this
    # module only needs the handful of attributes read below.
) -> str:
    """RobotEpisode -> LeRobot export (Shadow Robot Spatial Demonstration
    Pipeline spec sections 35, 37, 67). Additive sibling to `export_episode`
    above -- does not touch it or its call site in pipeline.py.

    Adds two things `export_episode` doesn't have: an `observation.state`
    column (spec section 37's mapping table asks for it; the old TEACH
    export never needed it). For Milestone 1's open-loop retargeted
    trajectories there's no independently-observed state distinct from the
    commanded action, so observation.state is set equal to action -- a
    documented simplification, not a claim the two are conceptually
    different data here.

    And a `meta/provenance.jsonl` (a new sibling file, not overloading
    LeRobot's own `meta/episodes.jsonl`) with every field spec section 67
    asks a verified episode to carry, so training data stays reproducible:
    which HumanEpisode it came from, which exact RobotBundle/AssetBundle it
    was verified against (by content hash, not just id), which
    retargeter/simulator version produced it.
    """
    if pa is None or pq is None:
        raise ImportError(
            "pyarrow is not installed on this platform (Linux only — see "
            "packages/ar-datapipe/README.md). ar_datapipe itself imports "
            "fine everywhere; only export_robot_episode needs it."
        )
    if not (len(timestamps_s) == len(actions) == len(gripper)):
        raise ValueError("timestamps_s, actions, and gripper must be the same length")
    if not actions:
        raise ValueError("cannot export an empty episode")

    action_dim = len(actions[0])
    if any(len(a) != action_dim for a in actions):
        raise ValueError("all action vectors must have the same dimensionality")

    data_dir = dataset_dir / "data" / "chunk-000"
    meta_dir = dataset_dir / "meta"
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    table = pa.table(
        {
            "frame_index": pa.array(range(len(timestamps_s)), type=pa.int64()),
            "timestamp": pa.array(timestamps_s, type=pa.float64()),
            "action": pa.array(actions, type=pa.list_(pa.float32(), action_dim)),
            "observation.state": pa.array(actions, type=pa.list_(pa.float32(), action_dim)),
            "gripper": pa.array(gripper, type=pa.float32()),
            "episode_index": pa.array([episode_index] * len(timestamps_s), type=pa.int64()),
        }
    )
    episode_path = data_dir / f"episode_{episode_index:06d}.parquet"
    pq.write_table(table, episode_path)

    info_path = meta_dir / "info.json"
    info = {
        "codebase_version": LEROBOT_CODEBASE_VERSION,
        "robot_type": robot_type,
        "fps": fps,
        "features": {
            "action": {"dtype": "float32", "shape": [action_dim]},
            "observation.state": {"dtype": "float32", "shape": [action_dim]},
            "gripper": {"dtype": "float32", "shape": [1]},
        },
    }
    info_path.write_text(json.dumps(info, indent=2) + "\n")

    tasks_path = meta_dir / "tasks.jsonl"
    with tasks_path.open("a") as f:
        f.write(json.dumps({"task_index": 0, "task": task}) + "\n")

    episodes_path = meta_dir / "episodes.jsonl"
    with episodes_path.open("a") as f:
        f.write(
            json.dumps(
                {"episode_index": episode_index, "tasks": [task], "length": len(timestamps_s)}
            )
            + "\n"
        )

    provenance_path = meta_dir / "provenance.jsonl"
    with provenance_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "episode_index": episode_index,
                    "robot_id": metadata.robot_id,
                    "robot_bundle_hash": metadata.robot_bundle_hash,
                    "source_human_episode_id": metadata.source_human_episode_id,
                    "human_episode_hash": metadata.human_episode_hash,
                    "task_id": metadata.task_id,
                    "asset_ids": list(metadata.asset_ids),
                    "asset_bundle_hash": metadata.asset_bundle_hash,
                    "simulator": metadata.simulator,
                    "retargeter_version": metadata.retargeter_version,
                    "task_version": metadata.task_version,
                    "created_at_ns": metadata.created_at_ns,
                }
            )
            + "\n"
        )

    return f"{dataset_dir.name}/episode_{episode_index:06d}"
