"""Repair recorded episodes in place, so an early take stays usable.

Two defects were found by measuring real recordings, and both are recoverable
from what is already stored -- which matters, because a demonstration cannot be
re-performed identically and throwing takes away is the expensive option.

**Swapped handedness.** `mirroredPreview` drove both where a hand was placed
and what it was called, but only the placement flip is real (the preview is
mirrored by a CSS transform, which never reaches MediaPipe). Episodes recorded
before that fix have correct geometry with the two labels transposed, so
swapping `left` <-> `right` restores them completely.

**Mug below the tabletop.** A held mug had no floor, so a hand dipping below
the table plane dragged it through. Clamping the stored object poses to the
tabletop is exactly what the live fix now does.

Detection is by measurement, not by date: an episode is diagnosed as swapped
when the hand labelled `right` sits on the -X side of the one labelled `left`
in the overwhelming majority of instants where both are tracked. +X is the
demonstrator's right, and holding your arms crossed for a whole take is not a
thing people do.

Dry-run by default. Nothing is written without `--apply`.

    uv run --no-sync python tools/repair_episodes.py                # report
    uv run --no-sync python tools/repair_episodes.py --apply        # fix

What this does NOT correct: episodes recorded before the palm ruler was
measured per-hand used a 10cm assumption against a 9.4cm palm, so their wrist
anchors sit about 6% further from the workspace centre than a fresh recording
would put them. That is roughly a centimetre at the edges -- below the p95
tracking jitter -- and undoing it would need the exact control volume and tilt
in force at record time, which is not stored. Mixed sets are fine at this
scale; it is noted here so nobody is surprised by it later.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ARVR_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ARVR_ROOT / "datasets" / "human_demos.sqlite"

# The tabletop is the z=0 plane (mugPickupLayout.TABLE_Z_M).
TABLE_Z_M = 0.0

# Below this fraction of correctly-ordered instants an episode is treated as
# swapped. Deliberately not 50%: a genuinely correct take sits near 100% and a
# swapped one near 0%, so anything ambiguous should be looked at by hand rather
# than silently "repaired".
SWAP_THRESHOLD = 0.10


def diagnose(conn: sqlite3.Connection, episode_id: str) -> tuple[int, int, int]:
    """(paired instants, instants with right hand on the +X side, mug samples
    below the tabletop)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS paired,
               SUM(CASE WHEN r.x_m > l.x_m THEN 1 ELSE 0 END) AS right_ok
        FROM hand_joints l
        JOIN hand_joints r
          ON l.episode_id = r.episode_id AND l.timestamp_ns = r.timestamp_ns
        WHERE l.episode_id = ?
          AND l.joint_name = 'wrist' AND r.joint_name = 'wrist'
          AND l.hand = 'left' AND r.hand = 'right'
        """,
        (episode_id,),
    ).fetchone()
    below = conn.execute(
        "SELECT COUNT(*) FROM object_states WHERE episode_id = ? AND z_m < ?",
        (episode_id, TABLE_Z_M - 1e-6),
    ).fetchone()[0]
    return int(row["paired"] or 0), int(row["right_ok"] or 0), int(below)


def swap_hands(conn: sqlite3.Connection, episode_id: str) -> int:
    """Transpose the left/right labels. Geometry is untouched -- it was right.

    Three passes through a placeholder rather than one `CASE`. Both tables are
    keyed on (episode, timestamp, hand), so at any instant where both hands are
    tracked a single-statement swap renames `left` onto a row that still exists
    as `right` and trips the uniqueness constraint half way through. The
    placeholder keeps every intermediate state unique.
    """
    n = 0
    for table in ("hand_frames", "hand_joints"):
        for src, dst in (("left", "__swap__"), ("right", "left"), ("__swap__", "right")):
            cur = conn.execute(
                f"UPDATE {table} SET hand = ? WHERE episode_id = ? AND hand = ?",
                (dst, episode_id, src),
            )
            if src != "__swap__":
                n += cur.rowcount
    return n


def clamp_to_table(conn: sqlite3.Connection, episode_id: str) -> int:
    cur = conn.execute(
        "UPDATE object_states SET z_m = ? WHERE episode_id = ? AND z_m < ?",
        (TABLE_Z_M, episode_id, TABLE_Z_M - 1e-6),
    )
    return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--no-backup", action="store_true", help="skip the .bak copy")
    args = ap.parse_args(argv)

    if not args.database.exists():
        print(f"no database at {args.database}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row

    episodes = [
        r["episode_id"]
        for r in conn.execute("SELECT episode_id FROM episodes ORDER BY exported_utc")
    ]
    plan: list[tuple[str, bool, int]] = []
    print(f"{args.database}  ({len(episodes)} episode(s))\n")
    for eid in episodes:
        paired, right_ok, below = diagnose(conn, eid)
        frac = right_ok / paired if paired else 1.0
        swapped = paired > 0 and frac <= SWAP_THRESHOLD
        print(f"  {eid[:8]}  handedness correct {100*frac:5.1f}% of {paired:>5} paired"
              f"   mug below table: {below}")
        if swapped:
            print("            -> hands are transposed; will swap labels")
        if below:
            print(f"            -> {below} object sample(s) below the tabletop; will clamp")
        if swapped or below:
            plan.append((eid, swapped, below))

    if not plan:
        print("\nnothing to repair.")
        return 0

    if not args.apply:
        print("\ndry run -- re-run with --apply to write these changes.")
        return 0

    if not args.no_backup:
        backup = args.database.with_suffix(args.database.suffix + ".bak")
        shutil.copy2(args.database, backup)
        print(f"\nbackup written to {backup.name}")

    with conn:
        for eid, swapped, below in plan:
            if swapped:
                n = swap_hands(conn, eid)
                print(f"  {eid[:8]}  swapped labels on {n} rows")
            if below:
                n = clamp_to_table(conn, eid)
                print(f"  {eid[:8]}  clamped {n} object sample(s) to the tabletop")

    print("\nre-checking:")
    for eid in episodes:
        paired, right_ok, below = diagnose(conn, eid)
        frac = 100 * right_ok / paired if paired else 100.0
        print(f"  {eid[:8]}  handedness correct {frac:5.1f}%   mug below table: {below}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
