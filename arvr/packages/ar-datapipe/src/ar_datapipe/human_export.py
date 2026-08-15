"""HumanEpisode -> LeRobot v3 export, with no robot in the loop.

Why this exists as a second export path
---------------------------------------
`export_robot_episode` (export.py) exports a *RobotEpisode*: joint angles
for a specific embodiment, produced by retargeting a human demonstration
onto a URDF and verifying it in simulation. That path is correct and
unchanged — but it cannot run today, because the robot hand this project is
being built for does not exist yet. There is no URDF to retarget onto, and
Pinocchio (the IK solver) is Linux-only, so `/spatial/episodes/{id}/finish`
answers 503 on the dev machine and would answer "no such robot" even on the
Spark.

The recorded human demonstration is nevertheless real, finished data. This
module publishes it at the layer it was captured at: the human hand's own
joint positions over time, in `struct_world` (right-handed, Z-up, meters),
with no embodiment-specific interpretation applied. That preserves the
two-layer split the pipeline is built on — a robot-independent
`HumanEpisode` that is never destroyed, and embodiment-specific
`RobotEpisode`s derived from it later — instead of blocking all training
data on one embodiment that has not been designed yet.

Feature vector layout
---------------------
One dataset row is **one instant in time**, not one hand-frame. A recording
session emits a left and a right `HandFrame` per tracked instant (see
`xr-web/src/hands.ts::readBothHands`), both stamped with the same frame
time; those are grouped back into a single row here.

Canonical joint ordering is `ar_contracts.HAND_JOINT_NAMES`, which is
`hands.ts`'s `HAND_JOINTS` (the 25 joints WebXR names) followed by
`PALM_JOINT` — 26 entries, wrist first, then thumb, index, middle, ring and
pinky each running metacarpal -> tip. The Python tuple and the TypeScript
constant already agree entry for entry; this module takes the Python tuple
as authoritative so the on-disk column order is defined by the contracts
package rather than by a browser file the backend never imports.

Hands are ordered `("left", "right")` — fixed and alphabetical, so the
layout does not depend on which hand happened to appear first in a
recording.

    action / observation.state   float32[156]
        left hand's 26 joint positions, then right hand's, each joint as
        (x, y, z) in meters. 2 hands * 26 joints * 3 = 156.
        `observation.state` carries the same values as `action`: at the
        human layer there is no observation distinct from the demonstrated
        configuration, and LeRobot policies expect the column to exist.
        This mirrors the documented simplification in `export_robot_episode`.

    joints_valid                 float32[52]
        1.0 if that joint's position in this row is real, 0.0 if it is not.
        2 hands * 26 joints, same ordering as above. Strictly binary on
        purpose: this column answers "is this number data?", which must not
        be a threshold question.

    observation.wrist_orientation_xyzw  float32[8]
        left wrist quaternion then right wrist quaternion, [x, y, z, w].
        See "orientation" below.

    observation.pinch_aperture_m float32[2]
        thumb-tip to index-tip distance in meters, left then right. NaN when
        either tip is unavailable.

    timestamp                    float64, seconds since the first row
    frame_index, episode_index   int64

**Orientation is deliberately almost entirely excluded.** Exporting a
quaternion per joint would add 2 * 26 * 4 = 208 floats and take the row from
156 to 364 — more than doubling every dataset — to encode information that
is largely already there: consecutive joint positions in a finger chain
determine that bone's direction, and all that a per-joint quaternion adds on
top is the roll about the bone. Roll only becomes meaningful once there is a
target hand to map it onto, and there is not one yet, so we would be paying
the storage now for a convention we cannot fix yet. The wrist is the
exception and *is* exported: it is the root of both chains, its orientation
is not implied by any other joint position, and it is already the 6-DoF
anchor the rest of the pipeline uses (`hands.ts::wristTarget`,
`ArmRetargeter`). Eight floats for the one orientation that is not
redundant.

Per-joint `confidence` is not exported. No `HandProvider` in this repo
currently emits a value below 1.0, so a column of 52 ones per row would be
storage without signal; `tracked` is what actually varies and it is captured
by `joints_valid`. Recorded here rather than silently dropped, because the
day a provider starts reporting real confidence this becomes a gap.

Missing data
------------
This is the correctness property the module exists to get right. A joint
position of (0, 0, 0) is a *real* position — the world origin — so
zero-filling an untracked joint teaches a policy that the hand teleported to
the origin. Untracked data is therefore represented twice over:

  * `joints_valid` is 0.0 for that joint, and
  * the position itself is NaN, not 0.0.

The mask is the contract; the NaN is the seatbelt, so that a consumer who
ignores the mask gets a loud, propagating NaN rather than a plausible-looking
zero. NaN is unambiguous here and not merely conventional: `PositionM`
already rejects non-finite components at the contract boundary, so no
recorded measurement can ever be a NaN that this sentinel would collide
with. A hand that is absent from an instant entirely — never tracked, or
dropped out mid-recording — is exactly the all-untracked case: its whole
26-entry mask block is 0.0 and its 78 positions are NaN. No hand is ever
implied to be at the origin, and no row silently pretends both hands were
present.

Determinism
-----------
Nothing time-of-day or environment dependent is written, so exporting the
same `HumanEpisode` twice produces byte-identical files. Provenance
identifies the source by content hash instead of by a wall clock.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from ar_contracts import HAND_JOINT_NAMES, HandFrame, HumanEpisode

from .export import require_pyarrow, write_lerobot_meta

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover - exercised via pytest.importorskip
    # Same deferral as export.py: importing this module must never be what
    # breaks a machine that only wants the contracts.
    pa = None
    pq = None

# The canonical layout constants. Everything below derives its widths from
# these rather than hard-coding 156/52/8, so the row and the declared
# feature shapes cannot drift apart.
HAND_ORDER: tuple[str, ...] = ("left", "right")
JOINT_ORDER: tuple[str, ...] = HAND_JOINT_NAMES
N_JOINTS = len(JOINT_ORDER)
POSITION_DIM = 3
QUATERNION_DIM = 4
ACTION_DIM = len(HAND_ORDER) * N_JOINTS * POSITION_DIM
MASK_DIM = len(HAND_ORDER) * N_JOINTS
WRIST_ORIENTATION_DIM = len(HAND_ORDER) * QUATERNION_DIM
APERTURE_DIM = len(HAND_ORDER)

# The sentinel that fills every position we have no measurement for. NaN and
# not 0.0, and not -1.0 either: those are both coordinates a hand could
# genuinely occupy, and a sentinel a policy can mistake for data is not a
# sentinel.
MISSING = float("nan")

HUMAN_EXPORTER_VERSION = "human_export@1"

# LeRobot's `info.json` requires a `robot_type`. This dataset has no robot in
# it by construction, and naming a real robot here would be a lie that a
# later reader would act on, so it names what was actually recorded.
HUMAN_ROBOT_TYPE = "human_hands"

_THUMB_TIP = "thumb-tip"
_INDEX_TIP = "index-finger-tip"
_WRIST = "wrist"


@dataclass(frozen=True)
class HumanExportResult:
    """What the caller needs to report and to find the data again."""

    dataset_id: str
    dataset_dir: Path
    episode_path: Path
    n_rows: int
    action_dim: int


def export_human_episode(
    dataset_dir: Path,
    episode: HumanEpisode,
    *,
    episode_index: int = 0,
    task: str | None = None,
) -> HumanExportResult:
    """Write `episode`'s recorded hands into `dataset_dir` as one LeRobot v3
    episode. See this module's docstring for the exact row layout.

    `task` defaults to the episode's own `task_id`; it is overridable because
    LeRobot's task string is the natural-language instruction a policy is
    conditioned on, which is not always the same string as our internal id.

    Raises `ValueError` for a recording that cannot honestly become training
    data, rather than writing a degenerate dataset: no hand frames at all, no
    tracked joint anywhere in the recording, two frames for the same hand at
    the same instant (a corrupt recording — we would have to pick one and the
    choice would be arbitrary), or frames captured in more than one
    coordinate frame (fusing `device_frame` and `struct_world` rows into one
    column would be a silent frame error).
    """
    require_pyarrow("export_human_episode")

    rows = _align_frames(episode.hand_frames)
    if not rows:
        raise ValueError(
            "cannot export a HumanEpisode with no hand frames — there is "
            "nothing recorded to train on"
        )

    timestamps_ns = sorted(rows)
    actions: list[list[float]] = []
    masks: list[list[float]] = []
    wrists: list[list[float]] = []
    apertures: list[list[float]] = []
    for ts in timestamps_ns:
        action, mask, wrist, aperture = _encode_instant(rows[ts])
        actions.append(action)
        masks.append(mask)
        wrists.append(wrist)
        apertures.append(aperture)

    if not any(any(m) for m in masks):
        raise ValueError(
            "cannot export a HumanEpisode in which no joint was ever tracked "
            "— every row would be entirely masked out"
        )

    t0 = timestamps_ns[0]
    timestamps_s = [(ts - t0) / 1e9 for ts in timestamps_ns]

    data_dir = dataset_dir / "data" / "chunk-000"
    data_dir.mkdir(parents=True, exist_ok=True)

    table = pa.table(
        {
            "frame_index": pa.array(range(len(timestamps_s)), type=pa.int64()),
            "timestamp": pa.array(timestamps_s, type=pa.float64()),
            "action": pa.array(actions, type=pa.list_(pa.float32(), ACTION_DIM)),
            "observation.state": pa.array(
                actions, type=pa.list_(pa.float32(), ACTION_DIM)
            ),
            "joints_valid": pa.array(masks, type=pa.list_(pa.float32(), MASK_DIM)),
            "observation.wrist_orientation_xyzw": pa.array(
                wrists, type=pa.list_(pa.float32(), WRIST_ORIENTATION_DIM)
            ),
            "observation.pinch_aperture_m": pa.array(
                apertures, type=pa.list_(pa.float32(), APERTURE_DIM)
            ),
            "episode_index": pa.array(
                [episode_index] * len(timestamps_s), type=pa.int64()
            ),
        }
    )
    episode_path = data_dir / f"episode_{episode_index:06d}.parquet"
    pq.write_table(table, episode_path)

    task_string = task or episode.metadata.task_id
    write_lerobot_meta(
        dataset_dir / "meta",
        robot_type=HUMAN_ROBOT_TYPE,
        fps=_fps_from_timestamps(timestamps_s),
        features={
            "action": {"dtype": "float32", "shape": [ACTION_DIM]},
            "observation.state": {"dtype": "float32", "shape": [ACTION_DIM]},
            "joints_valid": {"dtype": "float32", "shape": [MASK_DIM]},
            "observation.wrist_orientation_xyzw": {
                "dtype": "float32",
                "shape": [WRIST_ORIENTATION_DIM],
            },
            "observation.pinch_aperture_m": {
                "dtype": "float32",
                "shape": [APERTURE_DIM],
            },
        },
        task=task_string,
        episode_index=episode_index,
        n_rows=len(timestamps_s),
        # The layout is written into the dataset itself, not left to live
        # only in this docstring. A dataset that travels to another machine
        # (or to the teammate designing the robot hand) has to be able to say
        # what its 156 numbers mean without the exporter's source next to it.
        extra_info={
            "struct_layer": "human",
            "struct_hand_order": list(HAND_ORDER),
            "struct_joint_order": list(JOINT_ORDER),
            "struct_missing_value": "nan",
            "struct_validity_feature": "joints_valid",
        },
    )
    _write_human_provenance(
        dataset_dir / "meta",
        episode=episode,
        episode_index=episode_index,
        n_rows=len(timestamps_s),
    )

    return HumanExportResult(
        dataset_id=f"{dataset_dir.name}/episode_{episode_index:06d}",
        dataset_dir=dataset_dir,
        episode_path=episode_path,
        n_rows=len(timestamps_s),
        action_dim=ACTION_DIM,
    )


def _align_frames(frames: list[HandFrame]) -> dict[int, dict[str, HandFrame]]:
    """Group hand frames into instants keyed by timestamp.

    Grouping is on exact `timestamp_ns` equality. That is not a shortcut: a
    session reads both hands from one `XRFrame` and stamps them with that
    frame's single predicted display time, so the two sides are identical by
    construction (`hands.ts::readBothHands` -> `humanEpisodeRecorder`). If a
    future provider ever violates that, the failure mode is two adjacent
    half-masked rows — visibly wrong in the mask, and recoverable — rather
    than two unrelated instants silently fused into one row, which is not.
    """
    instants: dict[int, dict[str, HandFrame]] = {}
    coordinate_frames = {f.frame for f in frames}
    if len(coordinate_frames) > 1:
        raise ValueError(
            "hand frames span multiple coordinate frames "
            f"({sorted(coordinate_frames)}); an episode must be exported in "
            "one frame or the columns mean different things on different rows"
        )
    for frame in frames:
        by_side = instants.setdefault(frame.timestamp_ns, {})
        if frame.hand in by_side:
            raise ValueError(
                f"two {frame.hand!r} hand frames share timestamp "
                f"{frame.timestamp_ns}; the recording is corrupt and there is "
                "no non-arbitrary way to choose between them"
            )
        by_side[frame.hand] = frame
    return instants


def _encode_instant(
    by_side: dict[str, HandFrame],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """One instant -> (action, joints_valid, wrist orientations, apertures).

    Everything starts as missing and is only overwritten by a measurement
    that actually exists. Building it this way round — rather than filling in
    and then trying to remember to blank out what was absent — is what makes
    "untracked stays untracked" the default instead of something each branch
    has to remember.
    """
    action = [MISSING] * ACTION_DIM
    mask = [0.0] * MASK_DIM
    wrist = [MISSING] * WRIST_ORIENTATION_DIM
    aperture = [MISSING] * APERTURE_DIM

    for hand_index, side in enumerate(HAND_ORDER):
        frame = by_side.get(side)
        if frame is None:
            # This hand was not tracked at this instant. Leaving the block as
            # NaN/0.0 is the whole point: a dropped-out hand must be
            # distinguishable from a hand resting at the origin.
            continue

        position_base = hand_index * N_JOINTS * POSITION_DIM
        mask_base = hand_index * N_JOINTS
        for joint_index, name in enumerate(JOINT_ORDER):
            joint = frame.joints.get(name)
            # `tracked=False` is as untracked as absent. A provider that
            # reports a stale or extrapolated pose flags it that way instead
            # of omitting the key, and taking the pose anyway would be
            # exporting a guess as a measurement.
            if joint is None or not joint.tracked:
                continue
            offset = position_base + joint_index * POSITION_DIM
            action[offset : offset + POSITION_DIM] = list(joint.position_m)
            mask[mask_base + joint_index] = 1.0
            if name == _WRIST:
                wrist_offset = hand_index * QUATERNION_DIM
                wrist[wrist_offset : wrist_offset + QUATERNION_DIM] = list(
                    joint.orientation_xyzw
                )

        thumb = frame.joints.get(_THUMB_TIP)
        index = frame.joints.get(_INDEX_TIP)
        if thumb is not None and thumb.tracked and index is not None and index.tracked:
            aperture[hand_index] = math.dist(thumb.position_m, index.position_m)

    return action, mask, wrist, aperture


def _fps_from_timestamps(timestamps_s: list[float]) -> float:
    """Average sample rate, or 0.0 when a single row makes it undefined.

    Same derivation the robot pipeline uses (spatial_pipeline.run_spatial_episode);
    kept local rather than imported because importing the robot pipeline into
    the human-layer exporter would put a Pinocchio-shaped dependency behind a
    path whose entire reason to exist is not having one.
    """
    if len(timestamps_s) < 2:
        return 0.0
    avg_dt = (timestamps_s[-1] - timestamps_s[0]) / (len(timestamps_s) - 1)
    return round(1.0 / avg_dt, 2) if avg_dt > 0 else 0.0


def _write_human_provenance(
    meta_dir: Path, *, episode: HumanEpisode, episode_index: int, n_rows: int
) -> None:
    """Human-layer counterpart to `export_robot_episode`'s provenance line.

    The robot version records which URDF and which simulator produced the
    data; none of that exists here, so this records what does: which recorded
    episode this is, what captured it, and a content hash of the exact hand
    frames that were encoded. The hash — rather than a `created_at_ns` — is
    also what keeps the export byte-for-byte reproducible.
    """
    payload = [f.model_dump(mode="json") for f in episode.hand_frames]
    frames_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    line = {
        "layer": "human",
        "episode_index": episode_index,
        "source_human_episode_id": episode.metadata.episode_id,
        "human_episode_hash": frames_hash,
        "task_id": episode.metadata.task_id,
        "asset_id": episode.metadata.asset_id,
        "hand_provider": episode.metadata.hand_provider,
        "coordinate_frame": episode.metadata.coordinate_frame,
        "exporter_version": HUMAN_EXPORTER_VERSION,
        "n_rows": n_rows,
        "n_hand_frames": len(episode.hand_frames),
        "n_events": len(episode.events),
    }
    with (meta_dir / "provenance.jsonl").open("a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
