"""The red/blue ball-sorting success predicate (Quest teleop spec section 3).

Includes a drift guard that reads the real numbers back out of
`packages/xr-web/src/sortLayout.ts`. The TypeScript file is the source of
truth for the scene -- it is what the human sorts against -- and the Python
constants are a hand-kept mirror of it, so the one failure mode worth a test
is the two halves quietly disagreeing.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from ar_contracts import HumanEpisode, HumanEpisodeMetadata, ObjectState
from ar_datapipe.sort_task import (
    BALLS,
    BASKET_INTERIOR_M,
    BASKET_WALL_HEIGHT_M,
    BASKETS,
    SORT_TASK_ID,
    TABLE_Z_M,
    BallSpec,
    BasketSpec,
    SortTaskSpec,
    containing_basket,
    evaluate_sort_episode,
    evaluate_sort_task,
    final_object_states,
    inside_basket,
)

SORT_LAYOUT_TS = (
    Path(__file__).resolve().parent.parent / "packages" / "xr-web" / "src" / "sortLayout.ts"
)

RED_BASKET = next(b for b in BASKETS if b.id == "red_basket")
BLUE_BASKET = next(b for b in BASKETS if b.id == "blue_basket")

# Where a ball comes to rest on a basket floor, and where it rests on the
# table. Both are floor + radius; only the footprint tells them apart.
IN_BASKET_Z = TABLE_Z_M + 0.03
ON_TABLE = (0.17, 0.045, TABLE_Z_M + 0.03)


def _state(object_id: str, position: tuple[float, float, float], ts: int | None = None):
    return ObjectState(id=object_id, position_m=position, timestamp_ns=ts)


def _sorted_correctly() -> list[ObjectState]:
    """Every ball resting on the floor of its matching basket."""
    states = []
    for ball in BALLS:
        basket = RED_BASKET if ball.color == "red" else BLUE_BASKET
        states.append(_state(ball.id, (basket.center[0], basket.center[1], IN_BASKET_Z)))
    return states


# ---------------------------------------------------------------------------
# geometry mirror -- the TS file is the source of truth
# ---------------------------------------------------------------------------


def _ts_source() -> str:
    if not SORT_LAYOUT_TS.exists():
        pytest.skip(f"{SORT_LAYOUT_TS} not present (xr-web tree absent)")
    return SORT_LAYOUT_TS.read_text(encoding="utf-8")


def _ts_number(source: str, name: str) -> float:
    match = re.search(rf"export const {name} = ([0-9.]+);", source)
    assert match, f"{name} not found in sortLayout.ts"
    return float(match.group(1))


def test_scalar_geometry_matches_sort_layout_ts():
    source = _ts_source()
    assert _ts_number(source, "TABLE_Z_M") == TABLE_Z_M
    assert _ts_number(source, "BASKET_INTERIOR_M") == BASKET_INTERIOR_M
    assert _ts_number(source, "BASKET_WALL_HEIGHT_M") == BASKET_WALL_HEIGHT_M


def test_basket_placement_matches_sort_layout_ts():
    source = _ts_source()
    found = re.findall(
        r'\{\s*id:\s*"(\w+)",\s*color:\s*"(\w+)",\s*center:\s*\[([^\]]+)\]', source
    )
    assert found, "no basket literals found in sortLayout.ts"

    def _coord(token: str) -> float:
        token = token.strip()
        return TABLE_Z_M if token == "TABLE_Z_M" else float(token)

    mirrored = tuple(
        BasketSpec(
            id=basket_id,
            color=color,  # type: ignore[arg-type]
            center=tuple(_coord(c) for c in center.split(",")),  # type: ignore[arg-type]
        )
        for basket_id, color, center in found
    )
    assert mirrored == BASKETS


def test_ball_roster_matches_sort_layout_ts():
    source = _ts_source()
    found = re.findall(r'\{\s*id:\s*"(\w+)",\s*color:\s*"(\w+)",\s*start:', source)
    assert [BallSpec(id=i, color=c) for i, c in found] == list(BALLS)  # type: ignore[arg-type]


def test_task_id_matches_sort_layout_ts():
    assert f'SORT_TASK_ID = "{SORT_TASK_ID}"' in _ts_source()


# ---------------------------------------------------------------------------
# containment -- mirrors insideBasket in sortTask.ts
# ---------------------------------------------------------------------------


def test_ball_on_the_basket_floor_is_inside():
    assert inside_basket((RED_BASKET.center[0], RED_BASKET.center[1], IN_BASKET_Z), RED_BASKET)


def test_ball_carried_above_the_rim_is_not_inside():
    # The whole reason the test is a volume and not a footprint: carrying a
    # ball over a basket on the way somewhere else must not score.
    cx, cy, _ = RED_BASKET.center
    above_rim = (cx, cy, TABLE_Z_M + BASKET_WALL_HEIGHT_M + 0.01)
    assert not inside_basket(above_rim, RED_BASKET)


def test_the_rim_itself_counts_as_inside():
    at_rim = (RED_BASKET.center[0], RED_BASKET.center[1], TABLE_Z_M + BASKET_WALL_HEIGHT_M)
    assert inside_basket(at_rim, RED_BASKET)


def test_below_the_basket_floor_is_not_inside():
    below = (RED_BASKET.center[0], RED_BASKET.center[1], TABLE_Z_M - 0.001)
    assert not inside_basket(below, RED_BASKET)


def test_footprint_is_tight_to_the_interior():
    # A tenth of a millimetre either side of the wall, not the wall exactly:
    # `x - cx == half` is a floating-point coin flip in both this predicate
    # and the TypeScript one, so pinning it would test IEEE 754 rather than
    # agreement between the two implementations.
    half = BASKET_INTERIOR_M / 2
    cx, cy, _ = RED_BASKET.center
    assert inside_basket((cx + half - 1e-4, cy, IN_BASKET_Z), RED_BASKET)
    assert not inside_basket((cx + half + 1e-4, cy, IN_BASKET_Z), RED_BASKET)
    assert inside_basket((cx, cy - half + 1e-4, IN_BASKET_Z), RED_BASKET)
    assert not inside_basket((cx, cy - half - 1e-4, IN_BASKET_Z), RED_BASKET)


def test_the_two_baskets_do_not_overlap():
    assert containing_basket((RED_BASKET.center[0], 0.0, IN_BASKET_Z)) is None
    assert containing_basket(ON_TABLE) is None
    assert containing_basket(
        (RED_BASKET.center[0], RED_BASKET.center[1], IN_BASKET_Z)
    ) is RED_BASKET


# ---------------------------------------------------------------------------
# final_object_states -- reducing the time series
# ---------------------------------------------------------------------------


def test_timestamps_beat_list_order():
    states = [
        _state("red_ball_0", (0.0, 0.0, 0.5), ts=200),
        _state("red_ball_0", (0.0, 0.0, 0.1), ts=100),
    ]
    assert final_object_states(states)["red_ball_0"].position_m == (0.0, 0.0, 0.5)


def test_untimestamped_samples_fall_back_to_list_order():
    # The button task's single end-of-episode snapshot has no timestamp, so
    # capture order is the only ordering the record carries.
    states = [
        _state("red_ball_0", (0.0, 0.0, 0.1)),
        _state("red_ball_0", (0.0, 0.0, 0.5)),
    ]
    assert final_object_states(states)["red_ball_0"].position_m == (0.0, 0.0, 0.5)


def test_equal_timestamps_keep_the_later_sample():
    states = [
        _state("red_ball_0", (0.0, 0.0, 0.1), ts=100),
        _state("red_ball_0", (0.0, 0.0, 0.5), ts=100),
    ]
    assert final_object_states(states)["red_ball_0"].position_m == (0.0, 0.0, 0.5)


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------


def test_every_ball_in_its_matching_basket_succeeds():
    result = evaluate_sort_task(_sorted_correctly())
    assert result.success
    assert result.misplaced == ()
    assert result.unplaced == ()
    assert result.missing == ()
    assert result.placements["red_ball_0"] == "red_basket"
    assert result.placements["blue_ball_2"] == "blue_basket"


def test_a_ball_in_the_wrong_basket_fails_and_is_named():
    states = _sorted_correctly()
    states[0] = _state(
        "red_ball_0", (BLUE_BASKET.center[0], BLUE_BASKET.center[1], IN_BASKET_Z)
    )
    result = evaluate_sort_task(states)
    assert not result.success
    assert result.misplaced == ("red_ball_0",)
    assert result.unplaced == ()
    # The reason survives: it says which basket, not just "wrong".
    assert result.placements["red_ball_0"] == "blue_basket"


def test_a_ball_left_on_the_table_fails_as_unplaced():
    states = _sorted_correctly()
    states[0] = _state("red_ball_0", ON_TABLE)
    result = evaluate_sort_task(states)
    assert not result.success
    assert result.unplaced == ("red_ball_0",)
    assert result.misplaced == ()
    assert result.placements["red_ball_0"] is None


def test_an_unrecorded_ball_is_missing_not_unplaced():
    # A capture fault, not a human one, and the two must not be conflated.
    states = [s for s in _sorted_correctly() if s.id != "blue_ball_1"]
    result = evaluate_sort_task(states)
    assert not result.success
    assert result.missing == ("blue_ball_1",)
    assert result.unplaced == ()
    assert "blue_ball_1" not in result.placements


def test_only_the_final_pose_counts():
    # A ball dipped into the right basket mid-episode and then taken back out
    # is not sorted, however encouraging the middle of the record looked.
    states = list(_sorted_correctly())
    for i, state in enumerate(states):
        states[i] = _state(state.id, state.position_m, ts=100)
    states.append(_state("red_ball_0", ON_TABLE, ts=200))
    result = evaluate_sort_task(states)
    assert not result.success
    assert result.unplaced == ("red_ball_0",)


def test_objects_that_are_not_balls_are_ignored():
    states = [*_sorted_correctly(), _state("red_basket", RED_BASKET.center)]
    assert evaluate_sort_task(states).success


def test_empty_record_reports_every_ball_missing():
    result = evaluate_sort_task([])
    assert not result.success
    assert set(result.missing) == {ball.id for ball in BALLS}
    assert result.placements == {}


def test_a_custom_spec_scores_its_own_scene():
    # The predicate is parameterized, so a two-ball scene needs no change here.
    spec = SortTaskSpec(
        task_id="two_ball_toy",
        balls=(BallSpec(id="only_red", color="red"),),
        baskets=(BasketSpec(id="bin", color="red", center=(1.0, 1.0, 0.0)),),
    )
    assert evaluate_sort_task([_state("only_red", (1.0, 1.0, 0.02))], spec).success
    assert not evaluate_sort_task([_state("only_red", (0.0, 0.0, 0.02))], spec).success


def test_evaluate_sort_episode_reads_the_episode_without_touching_it():
    episode = HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=str(uuid.uuid4()),
            task_id=SORT_TASK_ID,
            asset_id="sort_scene_01",
            hand_provider="mock",
        ),
        object_states=_sorted_correctly(),
    )
    before = list(episode.object_states)
    assert evaluate_sort_episode(episode).success
    assert episode.object_states == before
