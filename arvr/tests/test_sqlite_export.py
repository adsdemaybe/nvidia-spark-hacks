"""The shareable-dataset export (`ar_datapipe.sqlite_export`).

The LeRobot parquet export is the training artifact, but it lands under
`arvr/data/`, which is gitignored -- so it cannot be handed to a collaborator
by pushing it. This exporter writes the same recording into a single SQLite
file that CAN be committed, which is the whole point of it existing.

These tests are the contract for that file: one row per hand per instant,
untracked joints absent rather than zero-filled, and re-exporting an episode
replacing it rather than duplicating it.
"""

from __future__ import annotations

import sqlite3

import pytest
from ar_contracts import (
    HandFrame,
    HumanEpisode,
    HumanEpisodeEvent,
    HumanEpisodeMetadata,
    ObjectState,
)
from ar_datapipe.sqlite_export import (
    SQLITE_SCHEMA_VERSION,
    export_human_episode_sqlite,
    list_episode_ids,
    load_human_episode,
    open_demo_database,
)

# episode_id is validated as a UUID by the contract, so the fixtures use real
# ones rather than readable strings.
EPISODE_A = "11111111-1111-4111-8111-111111111111"
EPISODE_B = "22222222-2222-4222-8222-222222222222"


def _frame(timestamp_ns: int, hand: str = "right", *, x: float = 0.26) -> HandFrame:
    return HandFrame(
        schema_version="1.0",
        timestamp_ns=timestamp_ns,
        source_device="webcam",
        hand=hand,
        frame="struct_world",
        joints={
            "wrist": {"position_m": [x, 0.0, 0.05], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "index-finger-tip": {
                "position_m": [x + 0.02, 0.0, 0.06],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        },
    )


def _episode(episode_id: str = EPISODE_A, n_frames: int = 3) -> HumanEpisode:
    return HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=episode_id,
            task_id="pick_up_mug",
            asset_id="mug_01",
            hand_provider="webcam",
        ),
        hand_frames=[_frame(1_000_000 * (i + 1)) for i in range(n_frames)],
        object_states=[
            ObjectState(
                id="mug_01",
                position_m=[0.26, 0.0, 0.0],
                timestamp_ns=1_000_000 * (i + 1),
            )
            for i in range(n_frames)
        ],
        events=[HumanEpisodeEvent(type="task_start", timestamp_ns=1_000_000)],
    )


def test_creates_a_single_file_that_can_be_committed(tmp_path):
    db_path = tmp_path / "human_demos.sqlite"
    export_human_episode_sqlite(db_path, _episode())
    assert db_path.is_file()
    assert db_path.stat().st_size > 0


def test_records_the_episode_and_its_provenance(tmp_path):
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(n_frames=3))
    with open_demo_database(db_path) as db:
        row = db.execute("SELECT * FROM episodes").fetchone()
    assert row["episode_id"] == EPISODE_A
    assert row["task_id"] == "pick_up_mug"
    assert row["asset_id"] == "mug_01"
    assert row["hand_provider"] == "webcam"
    assert row["coordinate_frame"] == "struct_world"
    assert row["n_hand_frames"] == 3


def test_one_row_per_hand_per_instant(tmp_path):
    db_path = tmp_path / "d.sqlite"
    episode = _episode(n_frames=0)
    episode.hand_frames = [
        _frame(1_000_000, "left"),
        _frame(1_000_000, "right"),
        _frame(2_000_000, "right"),
    ]
    export_human_episode_sqlite(db_path, episode)
    with open_demo_database(db_path) as db:
        rows = db.execute(
            "SELECT timestamp_ns, hand FROM hand_frames ORDER BY timestamp_ns, hand"
        ).fetchall()
    assert [(r["timestamp_ns"], r["hand"]) for r in rows] == [
        (1_000_000, "left"),
        (1_000_000, "right"),
        (2_000_000, "right"),
    ]


def test_joints_are_queryable_positions_not_an_opaque_blob(tmp_path):
    # A collaborator has to be able to read this without our code. Joint
    # positions therefore land as real columns, one row per joint.
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(n_frames=1))
    with open_demo_database(db_path) as db:
        row = db.execute(
            "SELECT * FROM hand_joints WHERE joint_name = 'wrist'"
        ).fetchone()
    assert row["x_m"] == pytest.approx(0.26)
    assert row["y_m"] == pytest.approx(0.0)
    assert row["z_m"] == pytest.approx(0.05)


def test_untracked_joints_are_absent_never_zero_filled(tmp_path):
    # The single most important correctness property carried over from the
    # parquet export: a zero position is a real position (the origin), so
    # zero-filling an occluded finger teaches a policy that it teleports to
    # the base of the workspace.
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(n_frames=1))
    with open_demo_database(db_path) as db:
        names = {r["joint_name"] for r in db.execute("SELECT joint_name FROM hand_joints")}
    assert names == {"wrist", "index-finger-tip"}
    assert "middle-finger-tip" not in names


def test_object_states_and_events_travel_with_the_episode(tmp_path):
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(n_frames=2))
    with open_demo_database(db_path) as db:
        objects = db.execute("SELECT * FROM object_states ORDER BY timestamp_ns").fetchall()
        events = db.execute("SELECT * FROM events").fetchall()
    assert [r["object_id"] for r in objects] == ["mug_01", "mug_01"]
    assert objects[0]["x_m"] == pytest.approx(0.26)
    assert [r["type"] for r in events] == ["task_start"]


def test_many_episodes_accumulate_in_one_file(tmp_path):
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(EPISODE_A))
    export_human_episode_sqlite(db_path, _episode(EPISODE_B))
    with open_demo_database(db_path) as db:
        rows = db.execute("SELECT episode_id FROM episodes ORDER BY episode_id")
        ids = [r["episode_id"] for r in rows]
    assert ids == sorted([EPISODE_A, EPISODE_B])


def test_re_exporting_replaces_rather_than_duplicating(tmp_path):
    # Exporting the same episode twice is a normal thing to do (a retry after
    # a failed request). Appending would silently double every frame of it,
    # and a duplicated demonstration is training data that is quietly wrong
    # rather than obviously broken.
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(EPISODE_A, n_frames=3))
    result = export_human_episode_sqlite(db_path, _episode(EPISODE_A, n_frames=2))
    with open_demo_database(db_path) as db:
        episodes = db.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"]
        frames = db.execute("SELECT COUNT(*) AS n FROM hand_frames").fetchone()["n"]
        joints = db.execute("SELECT COUNT(*) AS n FROM hand_joints").fetchone()["n"]
    assert episodes == 1
    assert frames == 2
    assert joints == 4  # 2 frames x 2 tracked joints
    assert result.n_hand_frames == 2


def test_reports_what_it_wrote(tmp_path):
    db_path = tmp_path / "d.sqlite"
    result = export_human_episode_sqlite(db_path, _episode(EPISODE_A, n_frames=3))
    assert result.episode_id == EPISODE_A
    assert result.database_path == db_path
    assert result.n_hand_frames == 3
    assert result.n_episodes_in_database == 1


def test_refuses_a_recording_with_no_hand_frames(tmp_path):
    # Same refusal as the parquet exporter, for the same reason: an episode
    # with nothing in it is not training data, and writing it makes the file
    # claim to hold a demonstration that does not exist.
    db_path = tmp_path / "d.sqlite"
    with pytest.raises(ValueError, match="no hand frames"):
        export_human_episode_sqlite(db_path, _episode(n_frames=0))


def test_stamps_a_schema_version_so_the_file_is_self_describing(tmp_path):
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode())
    with open_demo_database(db_path) as db:
        row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row["value"] == SQLITE_SCHEMA_VERSION


def test_round_trips_back_into_a_human_episode(tmp_path):
    # The file is the dataset, so it has to be readable back as one -- both to
    # regenerate a LeRobot export without re-recording, and so a repair can be
    # applied to stored takes rather than throwing them away.
    db_path = tmp_path / "d.sqlite"
    original = _episode(EPISODE_A, n_frames=3)
    export_human_episode_sqlite(db_path, original)

    loaded = load_human_episode(db_path, EPISODE_A)
    assert loaded.metadata.episode_id == original.metadata.episode_id
    assert loaded.metadata.task_id == original.metadata.task_id
    assert loaded.metadata.hand_provider == original.metadata.hand_provider
    assert len(loaded.hand_frames) == 3
    assert [f.timestamp_ns for f in loaded.hand_frames] == [
        f.timestamp_ns for f in original.hand_frames
    ]
    assert loaded.hand_frames[0].joints["wrist"].position_m == pytest.approx(
        original.hand_frames[0].joints["wrist"].position_m
    )
    assert len(loaded.object_states) == 3
    assert [e.type for e in loaded.events] == ["task_start"]


def test_round_trip_keeps_frames_ordered_by_time(tmp_path):
    # The artifact endpoint rejects out-of-order frames, so a reloaded episode
    # that lost its ordering could not be re-uploaded.
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(EPISODE_A, n_frames=5))
    loaded = load_human_episode(db_path, EPISODE_A)
    stamps = [f.timestamp_ns for f in loaded.hand_frames]
    assert stamps == sorted(stamps)


def test_lists_every_episode_in_the_file(tmp_path):
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(EPISODE_A))
    export_human_episode_sqlite(db_path, _episode(EPISODE_B))
    assert sorted(list_episode_ids(db_path)) == sorted([EPISODE_A, EPISODE_B])


def test_readable_by_plain_sqlite3_with_no_project_code(tmp_path):
    # The acceptance criterion for "my friend can use it": stdlib sqlite3,
    # no ar_datapipe, no pyarrow, no lerobot.
    db_path = tmp_path / "d.sqlite"
    export_human_episode_sqlite(db_path, _episode(n_frames=2))
    conn = sqlite3.connect(db_path)
    try:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM hand_joints WHERE joint_name = 'wrist'"
        ).fetchone()
    finally:
        conn.close()
    assert n == 2
