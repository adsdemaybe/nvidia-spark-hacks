# Shared demonstration data

`human_demos.sqlite` is the collected mug-pickup demonstrations, in a single
file you can commit and push. It appears here the first time an episode is
exported; it is not in the repo until somebody records something.

**Why this exists rather than just the LeRobot export.** `POST
/spatial/episodes/{id}/export-human` writes both: a LeRobot v3 parquet dataset
under `arvr/data/`, and this file. `arvr/data/` is gitignored — correctly, it
is generated and large — which means the parquet copy cannot be handed to a
collaborator by pushing it. This one can.

## Using it

Standard library only. No `ar_datapipe`, no pyarrow, no lerobot.

```python
import sqlite3

conn = sqlite3.connect("arvr/datasets/human_demos.sqlite")
conn.row_factory = sqlite3.Row

for row in conn.execute("SELECT episode_id, task_id, n_hand_frames FROM episodes"):
    print(dict(row))

# The wrist trajectory of one demonstration, in metres, struct_world:
wrist = conn.execute(
    """
    SELECT timestamp_ns, x_m, y_m, z_m
    FROM hand_joints
    WHERE episode_id = ? AND hand = 'right' AND joint_name = 'wrist'
    ORDER BY timestamp_ns
    """,
    (episode_id,),
).fetchall()
```

## Tables

| table | one row per |
|---|---|
| `episodes` | recording — task, asset, provider, frame count |
| `hand_frames` | hand per instant |
| `hand_joints` | **tracked** joint, per hand, per instant — position, orientation, confidence |
| `object_states` | object pose sample |
| `events` | `grasp_start`, `grasp_end`, `task_start`, `task_finish`, … |
| `meta` | `schema_version` of this file |

Coordinates are the project convention throughout: right-handed, **Z-up**,
**metres**, quaternion `[x, y, z, w]`, timestamps in **nanoseconds**. The
tabletop is `z = 0`.

## Two things to know before training on it

**An untracked joint has no row.** It is never zero-filled. A zero position is
a real position — the origin — so filling in an occluded finger would teach a
policy that it teleports to the base of the workspace. If you need a dense
array, decide the imputation yourself; the file will not decide it for you.

**One axis is inferred, not measured.** A single camera cannot see motion along
its own optical axis, so that axis comes from apparent hand size. Which axis it
is depends on where the camera was — see `arvr/DATA_COLLECTION.md`. Finger
articulation is trustworthy; that one absolute axis is approximate.

## Merging two people's recordings

Episode ids are UUIDs, so two collections never collide. Re-exporting an
episode replaces it rather than appending, so a retried save cannot double a
demonstration. But SQLite is a binary file and **git cannot merge it** — if you
both record against the same file you will get a conflict you have to resolve
by picking one side. Either take turns, or keep one file each and merge with:

```bash
sqlite3 mine.sqlite ".dump" | grep -v "^CREATE" | sqlite3 combined.sqlite
```
