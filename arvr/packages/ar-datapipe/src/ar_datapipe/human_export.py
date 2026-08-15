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

Object trajectory
-----------------
A hand reaching for nothing is not a demonstration. The single most
important thing a grasping policy has to observe is *where the object is*,
and a dataset of hand joints alone cannot express "reach toward the mug"
— every row would look identical whether the mug was in front of the hand
or across the room. So the recorded `HumanEpisode.object_states` time
series is exported alongside the hands, on the same rows.

With `K` tracked objects (`K` = 0 is handled below):

    observation.object_position_m       float32[K * 3]
        (x, y, z) in meters, per object, in the same `struct_world` frame
        as the hand joints.

    observation.object_orientation_xyzw float32[K * 4]
        [x, y, z, w] per object. Unlike the finger joints — where a
        per-joint quaternion is mostly redundant with the neighbouring
        joint positions — an object is a single rigid body whose rotation
        is not implied by anything else in the row, and how a mug is turned
        is exactly what decides where it can be grasped. So the full pose
        is exported, not just the position.

    object_valid                        float32[K]
        1.0 if this object had a *measured* sample at this row's instant,
        0.0 otherwise. Binary, for the same reason `joints_valid` is.

    observation.object_velocity_m_s     float32[K * 3]
        linear velocity in meters per second — see "velocity" below.

    observation.object_velocity_dt_s    float32[K]
        the interval, in seconds, the velocity on this row was differenced
        over. NaN wherever the velocity is.

    object_velocity_valid               float32[K]
        1.0 if this row's velocity was computed from two real measurements,
        0.0 otherwise.

Object identity and column order
--------------------------------
Column `i` of every object channel belongs to the object id at index `i` of
`struct_object_order` in `meta/info.json`. That ordering is the sorted set
of every distinct id appearing in `episode.object_states` — sorted, so it
is a property of *which objects were recorded* and not of the order the
recorder happened to serialize them in. Nothing here depends on dict or
list iteration order, and re-exporting the same episode always produces the
same column assignment.

Nothing assumes a single object. One mug today and a mug plus two baskets
later differ only in `K`, and a consumer that reads `struct_object_order`
rather than assuming a width keeps working across both.

`struct_object_order` is written even when it is empty, so a reader can
tell "this episode recorded no objects" from "this dataset predates object
export" — the absence of the key means the latter.

An episode with no object states at all still exports, and the six object
columns are **absent** from the parquet file rather than present with width
zero. A zero-width fixed-size list is a shape no consumer has a reason to
handle, and `struct_object_order: []` already says the same thing in the
place a consumer has to look anyway.

Velocity
--------
How fast the object was moving is the signal a contact/physics policy
needs and it is the one thing that cannot be recovered downstream: the
sampling interval is not constant, so a consumer differencing the position
column without knowing each row's real `dt` gets a wrong answer, and by
then the original timestamps may be gone.

The method is a **backward** finite difference, per object, in meters per
second:

    v[i] = (p[i] - p[j]) / ((t[i] - t[j]) / 1e9)

where `j` is the most recent *earlier row at which that object was
measured* — not simply the previous row. `dt` is exported per row per
object so the interval is never something a consumer has to assume.

Backward and not central, even though a central difference is the more
accurate estimator: a central difference at row `i` reads row `i + 1`, so
the observation would contain information from the future. A policy
trained on that cannot be run online, and the failure is silent — it shows
up as a model that works in replay and not on a robot.

Velocity is derived only from samples that landed on exported rows, which
makes the column reproducible from the file itself: a consumer who
differences `observation.object_position_m` against `timestamp` gets
exactly the exported numbers back.

Two cases produce no velocity, and both are marked invalid with NaN rather
than a zero:

  * **the first row an object is measured on.** There is no earlier
    measurement to difference against. A zero here would assert the object
    was stationary, which is a claim about the world that nothing was
    recorded to support — and "at rest" versus "unknown" is precisely the
    distinction a grasping policy must not have blurred for it.
  * **any row where the object was not measured**, including rows before it
    first appears and rows inside a dropout.

Across a *gap* — the object is measured, then missing for a while, then
measured again — velocity is emitted and valid, because it is a real
average velocity over a real interval, and `observation.object_velocity_dt_s`
states what that interval was. A consumer who considers a 400 ms average
too coarse to be an instantaneous velocity can filter on `dt`; a consumer
who is handed a silently-stale number cannot do anything at all. There is
no built-in staleness threshold here on purpose: any constant we picked
would be a policy decision baked irreversibly into the data.

Angular velocity is not exported. Differencing quaternions needs a
convention (which of the two sign-equivalent representations, body or
world frame) that only becomes decidable once there is a target embodiment
to consume it — the same argument the per-joint orientations above are
excluded under. Recorded here so it is a known gap and not an oversight.

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

Objects are treated identically, and the same reasoning applies with more
force: an object pose is a *target*, so a zero-filled or stale one does not
merely add noise, it teaches the policy to reach at the wrong place. Where
an object has no sample at a row's instant its position, orientation,
velocity and `dt` are NaN and both of its validity flags are 0.0.

A previous pose is never carried forward. Hold-last-value would be
defensible only with a separate channel saying "held, not measured", and
that channel would then have to be honoured by every consumer forever;
marking the row invalid says the same thing in one flag and cannot be
misread. An object that appears partway through a recording is therefore
invalid on every row before its first sample rather than being back-filled
from its first known pose.

Rows are defined by the *hand* timestamps, which is what keeps this export
additive: adding objects changed no existing row and no existing column. An
object sample whose `timestamp_ns` matches no hand instant, or which has no
`timestamp_ns` at all (the field is optional on `ObjectState`, and a
single end-of-episode snapshot legitimately leaves it None), therefore
cannot be placed on a row — placing it anywhere would be inventing a time.
Such samples are dropped from the trajectory, but the count is written to
`meta/provenance.jsonl` as `n_object_samples_unaligned` so the loss is a
recorded number rather than a silent one.

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

from ar_contracts import HAND_JOINT_NAMES, HandFrame, HumanEpisode, ObjectState

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

# Per-object widths. Unlike the hand widths above these cannot be baked into
# a module constant, because the number of objects is a property of the
# recording and not of the schema — an episode may carry one mug or a mug and
# three baskets. Everything below multiplies these by `len(object_order)`.
OBJECT_POSITION_DIM = POSITION_DIM
OBJECT_ORIENTATION_DIM = QUATERNION_DIM
OBJECT_VELOCITY_DIM = 3

# The object feature names, as constants, so the parquet column, the declared
# feature shape and `meta/info.json`'s pointer to the validity channel cannot
# drift apart by someone editing one string literal of three.
OBJECT_POSITION_FEATURE = "observation.object_position_m"
OBJECT_ORIENTATION_FEATURE = "observation.object_orientation_xyzw"
OBJECT_VALID_FEATURE = "object_valid"
OBJECT_VELOCITY_FEATURE = "observation.object_velocity_m_s"
OBJECT_VELOCITY_DT_FEATURE = "observation.object_velocity_dt_s"
OBJECT_VELOCITY_VALID_FEATURE = "object_velocity_valid"

# How `observation.object_velocity_m_s` was produced, recorded in
# `meta/info.json`. A velocity whose derivation a consumer has to guess at is
# a velocity a consumer will guess wrong about.
OBJECT_VELOCITY_METHOD = "backward_finite_difference"

NANOSECONDS_PER_SECOND = 1e9

# The sentinel that fills every position we have no measurement for. NaN and
# not 0.0, and not -1.0 either: those are both coordinates a hand could
# genuinely occupy, and a sentinel a policy can mistake for data is not a
# sentinel.
MISSING = float("nan")

# Bumped from @1 when the object trajectory columns were added. The addition
# is backward compatible on the hand side — every column @1 wrote is still
# written, unchanged — but a consumer reading a dataset needs to be able to
# tell whether object columns were merely empty or simply not supported by
# the exporter that ran, and a version string is the only place that answer
# survives.
HUMAN_EXPORTER_VERSION = "human_export@2"

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
    # Which object occupies which column slot, in column order. Returned as
    # well as written to `meta/info.json` so a caller that just exported an
    # episode can report what it captured without re-reading the metadata it
    # only just wrote. Empty for an episode that recorded no objects.
    object_order: tuple[str, ...] = ()


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
    choice would be arbitrary), frames captured in more than one coordinate
    frame (fusing `device_frame` and `struct_world` rows into one column
    would be a silent frame error), or two *disagreeing* poses for one object
    at one instant (same argument as the duplicate hand frame).
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

    # Objects are resolved only after the hand rows exist, because the row
    # grid is the hand's: an object sample is aligned *to* an instant that the
    # hands already established, never the other way round. That ordering is
    # what makes this addition unable to move an existing row.
    object_order = _object_column_order(episode.object_states)
    object_samples, n_object_samples_unaligned = _align_object_states(
        episode.object_states, set(timestamps_ns)
    )
    object_columns = _encode_object_track(timestamps_ns, object_samples, object_order)

    t0 = timestamps_ns[0]
    timestamps_s = [(ts - t0) / NANOSECONDS_PER_SECOND for ts in timestamps_ns]

    data_dir = dataset_dir / "data" / "chunk-000"
    data_dir.mkdir(parents=True, exist_ok=True)

    columns = {
        "frame_index": pa.array(range(len(timestamps_s)), type=pa.int64()),
        "timestamp": pa.array(timestamps_s, type=pa.float64()),
        "action": pa.array(actions, type=pa.list_(pa.float32(), ACTION_DIM)),
        "observation.state": pa.array(actions, type=pa.list_(pa.float32(), ACTION_DIM)),
        "joints_valid": pa.array(masks, type=pa.list_(pa.float32(), MASK_DIM)),
        "observation.wrist_orientation_xyzw": pa.array(
            wrists, type=pa.list_(pa.float32(), WRIST_ORIENTATION_DIM)
        ),
        "observation.pinch_aperture_m": pa.array(
            apertures, type=pa.list_(pa.float32(), APERTURE_DIM)
        ),
    }
    # Appended, not interleaved: the hand columns keep both their identity and
    # their position in the schema, so a reader written against a @1 dataset
    # sees exactly what it saw before with extra columns after it.
    for name, values, width in _object_column_specs(object_columns, object_order):
        columns[name] = pa.array(values, type=pa.list_(pa.float32(), width))
    columns["episode_index"] = pa.array(
        [episode_index] * len(timestamps_s), type=pa.int64()
    )

    table = pa.table(columns)
    episode_path = data_dir / f"episode_{episode_index:06d}.parquet"
    pq.write_table(table, episode_path)

    features = {
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
    }
    # Derived from the same specs the parquet columns came from, so a declared
    # shape cannot disagree with the column it describes.
    for name, _values, width in _object_column_specs(object_columns, object_order):
        features[name] = {"dtype": "float32", "shape": [width]}

    task_string = task or episode.metadata.task_id
    write_lerobot_meta(
        dataset_dir / "meta",
        robot_type=HUMAN_ROBOT_TYPE,
        fps=_fps_from_timestamps(timestamps_s),
        features=features,
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
            # The object columns are only interpretable through this list:
            # slot i of every object channel is this list's entry i. Written
            # even when empty — see the module docstring on why "[] " and
            # "key absent" have to mean different things.
            "struct_object_order": list(object_order),
            "struct_object_validity_feature": OBJECT_VALID_FEATURE,
            "struct_object_velocity_validity_feature": OBJECT_VELOCITY_VALID_FEATURE,
            "struct_object_velocity_method": OBJECT_VELOCITY_METHOD,
            "struct_object_velocity_interval_feature": OBJECT_VELOCITY_DT_FEATURE,
        },
    )
    _write_human_provenance(
        dataset_dir / "meta",
        episode=episode,
        episode_index=episode_index,
        n_rows=len(timestamps_s),
        object_order=object_order,
        n_object_samples_unaligned=n_object_samples_unaligned,
    )

    return HumanExportResult(
        dataset_id=f"{dataset_dir.name}/episode_{episode_index:06d}",
        dataset_dir=dataset_dir,
        episode_path=episode_path,
        n_rows=len(timestamps_s),
        action_dim=ACTION_DIM,
        object_order=object_order,
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


@dataclass(frozen=True)
class _ObjectColumns:
    """The six per-row object channels, each already laid out flat.

    Kept as one object rather than a six-tuple because they are produced
    together, consumed together and must stay the same length as each other;
    a tuple that size is a place for two of its members to get swapped at a
    call site and for nothing to notice.
    """

    position: list[list[float]]
    orientation: list[list[float]]
    valid: list[list[float]]
    velocity: list[list[float]]
    velocity_dt: list[list[float]]
    velocity_valid: list[list[float]]


def _object_column_specs(
    columns: _ObjectColumns, object_order: tuple[str, ...]
) -> list[tuple[str, list[list[float]], int]]:
    """(feature name, per-row values, fixed list width) for each object channel.

    One definition serves both the parquet schema and `info.json`'s declared
    feature shapes, so the two cannot disagree about a width. Empty for an
    episode with no objects, which is what makes the columns *absent* rather
    than zero-width.
    """
    if not object_order:
        return []
    n = len(object_order)
    return [
        (OBJECT_POSITION_FEATURE, columns.position, n * OBJECT_POSITION_DIM),
        (OBJECT_ORIENTATION_FEATURE, columns.orientation, n * OBJECT_ORIENTATION_DIM),
        (OBJECT_VALID_FEATURE, columns.valid, n),
        (OBJECT_VELOCITY_FEATURE, columns.velocity, n * OBJECT_VELOCITY_DIM),
        (OBJECT_VELOCITY_DT_FEATURE, columns.velocity_dt, n),
        (OBJECT_VELOCITY_VALID_FEATURE, columns.velocity_valid, n),
    ]


def _object_column_order(states: list[ObjectState]) -> tuple[str, ...]:
    """The object ids, in the order they occupy column slots.

    Sorted, and derived from the set of ids rather than from the sequence, so
    the mapping from object to column depends only on *which* objects the
    episode recorded. Deriving it from first-appearance order would mean two
    recordings of the same scene could disagree about which columns the mug
    lives in purely because the recorder started sampling a different object
    first — and a consumer comparing two episodes would silently compare the
    mug against a basket.
    """
    return tuple(sorted({state.id for state in states}))


def _align_object_states(
    states: list[ObjectState], row_timestamps: set[int]
) -> tuple[dict[int, dict[str, ObjectState]], int]:
    """Group object samples onto the hand rows' instants.

    Returns the samples keyed by instant then by object id, plus a count of
    the samples that could not be placed on any row. Alignment is exact
    timestamp equality with a row, for the same reason `_align_frames` groups
    hands that way: the capture client records object poses inside the same
    frame callback that reads the hands and stamps both with that frame's one
    time, so equality is exact by construction rather than by luck. Nearest-
    neighbour matching with a tolerance would paper over a recorder that had
    genuinely stopped doing that, and the resulting rows would pair a hand
    with an object pose from a different instant — the exact error this
    function exists to prevent.

    Samples that match no row (or carry no timestamp at all) are counted, not
    dropped silently and not snapped to a neighbouring row.
    """
    aligned: dict[int, dict[str, ObjectState]] = {}
    unaligned = 0
    for state in states:
        if state.timestamp_ns is None or state.timestamp_ns not in row_timestamps:
            unaligned += 1
            continue
        by_id = aligned.setdefault(state.timestamp_ns, {})
        prior = by_id.get(state.id)
        if prior is not None:
            # A recorder that emits the same pose twice for one object in one
            # frame is redundant but harmless — both samples say the same
            # thing, so there is nothing to choose between. Two *different*
            # poses at one instant is the corrupt case: the object cannot have
            # been in two places, and picking either one would be inventing
            # data. Same verdict `_align_frames` reaches for duplicate hands.
            if (
                prior.position_m != state.position_m
                or prior.orientation_xyzw != state.orientation_xyzw
            ):
                raise ValueError(
                    f"object {state.id!r} has two different poses at timestamp "
                    f"{state.timestamp_ns}; the recording is corrupt and there "
                    "is no non-arbitrary way to choose between them"
                )
            continue
        by_id[state.id] = state
    return aligned, unaligned


def _encode_object_track(
    timestamps_ns: list[int],
    samples: dict[int, dict[str, ObjectState]],
    object_order: tuple[str, ...],
) -> _ObjectColumns:
    """The aligned object samples -> the six per-row object channels.

    Written as one forward pass over the rows because velocity is inherently
    stateful: each object carries the instant and position of the last row it
    was actually measured on, which is what lets a dropout produce a correct
    average velocity when the object comes back rather than a difference
    against whatever happened to be in the previous row.

    Like `_encode_instant`, every channel starts as missing and is only
    overwritten by something measured, so "no data" is the default rather than
    a case each branch has to remember to handle.
    """
    n = len(object_order)
    columns = _ObjectColumns([], [], [], [], [], [])
    if not n:
        return columns

    # object index -> (instant, position) of the most recent measured row.
    last_measured: dict[int, tuple[int, tuple[float, float, float]]] = {}

    for ts in timestamps_ns:
        at_instant = samples.get(ts, {})
        position = [MISSING] * (n * OBJECT_POSITION_DIM)
        orientation = [MISSING] * (n * OBJECT_ORIENTATION_DIM)
        valid = [0.0] * n
        velocity = [MISSING] * (n * OBJECT_VELOCITY_DIM)
        velocity_dt = [MISSING] * n
        velocity_valid = [0.0] * n

        for index, object_id in enumerate(object_order):
            state = at_instant.get(object_id)
            if state is None:
                # Not measured here. Leaving the whole slot missing — pose and
                # velocity alike — is the point: this must not be confusable
                # with an object measured at rest at the origin, and it must
                # not silently inherit the pose it had a moment ago.
                continue

            position[index * OBJECT_POSITION_DIM : (index + 1) * OBJECT_POSITION_DIM] = (
                list(state.position_m)
            )
            orientation[
                index * OBJECT_ORIENTATION_DIM : (index + 1) * OBJECT_ORIENTATION_DIM
            ] = list(state.orientation_xyzw)
            valid[index] = 1.0

            prior = last_measured.get(index)
            if prior is not None:
                prior_ts, prior_position = prior
                # `timestamps_ns` is the sorted list of distinct row instants,
                # and `prior_ts` is a strictly earlier one, so this interval is
                # always positive — no zero-division guard is needed and adding
                # one would only hide it if that ever stopped being true.
                dt_s = (ts - prior_ts) / NANOSECONDS_PER_SECOND
                velocity[
                    index * OBJECT_VELOCITY_DIM : (index + 1) * OBJECT_VELOCITY_DIM
                ] = [
                    (now - before) / dt_s
                    for now, before in zip(
                        state.position_m, prior_position, strict=True
                    )
                ]
                velocity_dt[index] = dt_s
                velocity_valid[index] = 1.0
            # No `else`: the first row an object is measured on has nothing to
            # difference against, and its velocity stays NaN/invalid rather
            # than becoming a zero that asserts the object started at rest.

            last_measured[index] = (ts, state.position_m)

        columns.position.append(position)
        columns.orientation.append(orientation)
        columns.valid.append(valid)
        columns.velocity.append(velocity)
        columns.velocity_dt.append(velocity_dt)
        columns.velocity_valid.append(velocity_valid)

    return columns


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
    meta_dir: Path,
    *,
    episode: HumanEpisode,
    episode_index: int,
    n_rows: int,
    object_order: tuple[str, ...],
    n_object_samples_unaligned: int,
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
    # A second, separate hash rather than folding the object states into
    # `human_episode_hash`. That field's value is already recorded in datasets
    # exported by @1 and is quoted as "the hash of the hand frames"; silently
    # redefining what it covers would make two provenance lines that disagree
    # look like two different recordings. The export now depends on the object
    # states too, so their contribution has to be hashed somewhere — here.
    object_payload = [s.model_dump(mode="json") for s in episode.object_states]
    objects_hash = hashlib.sha256(
        json.dumps(object_payload, sort_keys=True).encode()
    ).hexdigest()
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
        "object_states_hash": objects_hash,
        "object_order": list(object_order),
        "n_object_states": len(episode.object_states),
        # How many recorded object poses did not make it into the dataset
        # because they matched no hand instant (or carried no timestamp).
        # Recorded as a number so a capture bug that half-desynchronises the
        # object and hand streams is visible in the artifact rather than only
        # in a diff of row counts nobody computes.
        "n_object_samples_unaligned": n_object_samples_unaligned,
    }
    with (meta_dir / "provenance.jsonl").open("a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
