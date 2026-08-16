"""Human demonstrations as one shareable SQLite file.

Why this exists alongside the LeRobot export: `human_export.py` writes the
training artifact, and it writes it under `arvr/data/`, which `.gitignore`
excludes. That is the right call for generated parquet -- but it means the
recordings cannot be handed to a collaborator by pushing them, which is
exactly what a two-person project wants to do with a dataset.

So this is a second, independent exit from the same recording, in the spirit
of `/export-human` being a second exit alongside `/finish`: one file, no
generated directory tree, small enough to commit, and readable with nothing
but the Python standard library. A collaborator needs no `ar_datapipe`, no
pyarrow and no lerobot to open it -- which is the entire acceptance criterion.

It is deliberately NOT a replacement for the parquet export. Parquet is what a
policy trains on; this is what gets shared and inspected. Both are written from
the same `HumanEpisode`, so neither can drift from the recording.

Design notes that are load-bearing rather than taste:

- **Joint positions are columns, not a blob.** A JSON or binary column would
  be smaller, but the point of this file is that somebody else can open it and
  ask questions in SQL. One row per joint per hand per instant is the shape
  that makes `SELECT ... WHERE joint_name = 'wrist'` work.
- **An untracked joint has no row.** Never a row of zeros. A zero position is
  a real position -- the origin -- so zero-filling an occluded finger teaches a
  policy that it teleports to the base of the workspace. Absence is the honest
  encoding, and it matches the parquet export's NaN-plus-invalid-flag for the
  same reason.
- **Re-export replaces.** Exporting an episode twice is normal (a retry after a
  failed request), and appending would silently double every frame of that
  demonstration. Duplicated data is quietly wrong rather than obviously broken,
  which is the worse failure.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ar_contracts import HumanEpisode

# Bumped whenever the table layout changes in a way a reader would notice.
# Stamped into the file so a collaborator holding an old copy can tell.
SQLITE_SCHEMA_VERSION = "1.0"

# Default location. Deliberately NOT under `arvr/data/`, which .gitignore
# excludes -- a dataset nobody can push is the problem this module solves.
DEFAULT_DATABASE_PATH = Path("datasets/human_demos.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id       TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    asset_id         TEXT NOT NULL,
    hand_provider    TEXT NOT NULL,
    coordinate_frame TEXT NOT NULL,
    status           TEXT NOT NULL,
    exported_utc     TEXT NOT NULL,
    n_hand_frames    INTEGER NOT NULL
);

-- One row per hand per instant. Never one row per hand-and-object, and never
-- one row per hand across all time: an instant is the unit a policy sees.
CREATE TABLE IF NOT EXISTS hand_frames (
    episode_id   TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    timestamp_ns INTEGER NOT NULL,
    hand         TEXT NOT NULL,
    source_device TEXT NOT NULL,
    PRIMARY KEY (episode_id, timestamp_ns, hand)
);

-- One row per tracked joint. A joint the tracker did not see has NO ROW here.
CREATE TABLE IF NOT EXISTS hand_joints (
    episode_id   TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    timestamp_ns INTEGER NOT NULL,
    hand         TEXT NOT NULL,
    joint_name   TEXT NOT NULL,
    x_m REAL NOT NULL, y_m REAL NOT NULL, z_m REAL NOT NULL,
    qx REAL NOT NULL, qy REAL NOT NULL, qz REAL NOT NULL, qw REAL NOT NULL,
    confidence REAL NOT NULL,
    tracked INTEGER NOT NULL,
    PRIMARY KEY (episode_id, timestamp_ns, hand, joint_name)
);

CREATE TABLE IF NOT EXISTS object_states (
    episode_id   TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    timestamp_ns INTEGER,
    object_id    TEXT NOT NULL,
    x_m REAL NOT NULL, y_m REAL NOT NULL, z_m REAL NOT NULL,
    qx REAL NOT NULL, qy REAL NOT NULL, qz REAL NOT NULL, qw REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    episode_id   TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    timestamp_ns INTEGER NOT NULL,
    type         TEXT NOT NULL,
    object_id    TEXT,
    container_id TEXT
);

CREATE INDEX IF NOT EXISTS hand_joints_by_name
    ON hand_joints (joint_name, episode_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS object_states_by_episode
    ON object_states (episode_id, timestamp_ns);
"""


@dataclass(frozen=True)
class SqliteExportResult:
    """What a single export wrote, for the caller to report back."""

    episode_id: str
    database_path: Path
    n_hand_frames: int
    n_joint_rows: int
    n_episodes_in_database: int


@contextmanager
def open_demo_database(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open the demo database with `row_factory` set, always closing it.

    Rows come back as `sqlite3.Row` so callers index by column name; every
    query here is read by a human at some point, and positional indexing into
    a fourteen-column joint row is how a reader ends up silently plotting the
    quaternion's x as a position.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SQLITE_SCHEMA_VERSION,),
    )


def list_episode_ids(database_path: Path | str) -> list[str]:
    """Every episode in the file, oldest export first."""
    with open_demo_database(database_path) as conn:
        return [
            r["episode_id"]
            for r in conn.execute("SELECT episode_id FROM episodes ORDER BY exported_utc")
        ]


def load_human_episode(database_path: Path | str, episode_id: str) -> HumanEpisode:
    """Read one episode back out as the `HumanEpisode` it was written from.

    The file is the dataset, so it has to be readable back as one. Two things
    need that: regenerating a LeRobot export without re-recording, and applying
    a repair to takes that are already on disk instead of discarding them.

    Frames come back ordered by timestamp. The artifact endpoint rejects
    out-of-order frames, so an episode that lost its ordering on the way back
    could not be re-uploaded -- and a reader is entitled to assume a time
    series is in time order.
    """
    from ar_contracts import (
        HandFrame,
        HumanEpisode,
        HumanEpisodeEvent,
        HumanEpisodeMetadata,
        ObjectState,
    )

    with open_demo_database(database_path) as conn:
        meta = conn.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if meta is None:
            raise KeyError(f"no episode {episode_id!r} in {database_path}")

        joints_by_frame: dict[tuple[int, str], dict[str, dict]] = {}
        for r in conn.execute(
            "SELECT * FROM hand_joints WHERE episode_id = ? "
            "ORDER BY timestamp_ns, hand, joint_name",
            (episode_id,),
        ):
            key = (r["timestamp_ns"], r["hand"])
            joints_by_frame.setdefault(key, {})[r["joint_name"]] = {
                "position_m": [r["x_m"], r["y_m"], r["z_m"]],
                "orientation_xyzw": [r["qx"], r["qy"], r["qz"], r["qw"]],
                "confidence": r["confidence"],
                "tracked": bool(r["tracked"]),
            }

        hand_frames = [
            HandFrame(
                timestamp_ns=r["timestamp_ns"],
                source_device=r["source_device"],
                hand=r["hand"],
                frame=meta["coordinate_frame"],
                joints=joints_by_frame.get((r["timestamp_ns"], r["hand"]), {}),
            )
            for r in conn.execute(
                "SELECT * FROM hand_frames WHERE episode_id = ? ORDER BY timestamp_ns, hand",
                (episode_id,),
            )
        ]

        object_states = [
            ObjectState(
                id=r["object_id"],
                position_m=[r["x_m"], r["y_m"], r["z_m"]],
                orientation_xyzw=[r["qx"], r["qy"], r["qz"], r["qw"]],
                timestamp_ns=r["timestamp_ns"],
            )
            for r in conn.execute(
                "SELECT * FROM object_states WHERE episode_id = ? ORDER BY timestamp_ns",
                (episode_id,),
            )
        ]

        events = [
            HumanEpisodeEvent(
                type=r["type"],
                timestamp_ns=r["timestamp_ns"],
                object_id=r["object_id"],
                container_id=r["container_id"],
            )
            for r in conn.execute(
                "SELECT * FROM events WHERE episode_id = ? ORDER BY timestamp_ns",
                (episode_id,),
            )
        ]

    return HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=meta["episode_id"],
            task_id=meta["task_id"],
            asset_id=meta["asset_id"],
            hand_provider=meta["hand_provider"],
            coordinate_frame=meta["coordinate_frame"],
            status=meta["status"],
        ),
        hand_frames=hand_frames,
        object_states=object_states,
        events=events,
    )


def export_human_episode_sqlite(
    database_path: Path | str,
    episode: HumanEpisode,
) -> SqliteExportResult:
    """Write `episode` into the shareable SQLite file at `database_path`.

    Creates the file and its schema on first use, and accumulates episodes
    across calls -- the file is the dataset, not one episode's export.

    Re-exporting an episode already present REPLACES it. See the module
    docstring for why appending would be the wrong default.

    Raises `ValueError` for a recording with no hand frames, matching
    `export_human_episode`'s refusal to write a dataset that claims to hold a
    demonstration it does not have.
    """
    if not episode.hand_frames:
        raise ValueError(
            f"episode {episode.metadata.episode_id!r} has no hand frames; "
            "there is no demonstration to export"
        )

    path = Path(database_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    episode_id = episode.metadata.episode_id
    exported_utc = datetime.now(UTC).isoformat()

    with open_demo_database(path) as conn:
        # Foreign keys are OFF by default in sqlite3 and have to be asked for
        # per connection -- without this the ON DELETE CASCADE below silently
        # does nothing and a re-export leaves the old episode's frames behind,
        # orphaned and indistinguishable from real data.
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(conn)

        with conn:  # one transaction: a half-written episode is not a dataset
            conn.execute("DELETE FROM episodes WHERE episode_id = ?", (episode_id,))
            conn.execute(
                "INSERT INTO episodes (episode_id, task_id, asset_id, hand_provider, "
                "coordinate_frame, status, exported_utc, n_hand_frames) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    episode.metadata.task_id,
                    episode.metadata.asset_id,
                    episode.metadata.hand_provider,
                    episode.metadata.coordinate_frame,
                    episode.metadata.status,
                    exported_utc,
                    len(episode.hand_frames),
                ),
            )

            joint_rows = 0
            for frame in episode.hand_frames:
                conn.execute(
                    "INSERT OR REPLACE INTO hand_frames "
                    "(episode_id, timestamp_ns, hand, source_device) VALUES (?, ?, ?, ?)",
                    (episode_id, frame.timestamp_ns, frame.hand, frame.source_device),
                )
                for name, joint in frame.joints.items():
                    px, py, pz = joint.position_m
                    qx, qy, qz, qw = joint.orientation_xyzw
                    conn.execute(
                        "INSERT OR REPLACE INTO hand_joints (episode_id, timestamp_ns, "
                        "hand, joint_name, x_m, y_m, z_m, qx, qy, qz, qw, confidence, "
                        "tracked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            episode_id, frame.timestamp_ns, frame.hand, name,
                            px, py, pz, qx, qy, qz, qw,
                            joint.confidence, int(joint.tracked),
                        ),
                    )
                    joint_rows += 1

            for state in episode.object_states:
                ox, oy, oz = state.position_m
                qx, qy, qz, qw = state.orientation_xyzw
                conn.execute(
                    "INSERT INTO object_states (episode_id, timestamp_ns, object_id, "
                    "x_m, y_m, z_m, qx, qy, qz, qw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        episode_id, state.timestamp_ns, state.id,
                        ox, oy, oz, qx, qy, qz, qw,
                    ),
                )

            for event in episode.events:
                conn.execute(
                    "INSERT INTO events (episode_id, timestamp_ns, type, object_id, "
                    "container_id) VALUES (?, ?, ?, ?, ?)",
                    (
                        episode_id, event.timestamp_ns, event.type,
                        event.object_id, event.container_id,
                    ),
                )

        n_episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    return SqliteExportResult(
        episode_id=episode_id,
        database_path=path,
        n_hand_frames=len(episode.hand_frames),
        n_joint_rows=joint_rows,
        n_episodes_in_database=n_episodes,
    )
