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

import pyarrow as pa
import pyarrow.parquet as pq

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
