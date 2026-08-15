"""The red/blue ball-sorting success predicate — Quest teleop spec section 3.

`ar_contracts.TaskSpec` is a goal point plus a tolerance: "did the tool tip
end up near here". That is the whole predicate for the button task, and for
this one it is a projection rather than a description -- sorting six balls is
six containments, and no single goal point expresses it. So this module adds
a *separate* predicate instead of widening `TaskSpec`, which
`MuJoCoSimulationProvider`, `IsaacSimulationProvider` and
`ar_datapipe.pipeline` already destructure field-by-field. The two coexist:
the simulator still verifies a goal point, and this decides whether the sort
itself succeeded. Nothing that uses `TaskSpec` has to change.

No model judgement decides success here (STRUCT rule: gates decide, not
judgment). Containment is plain arithmetic on the recorded `ObjectState`
time series, so a rejected episode always carries the measurable reason.

GEOMETRY IS MIRRORED, NOT SHARED. `packages/xr-web/src/sortLayout.ts` is the
source of truth for where the baskets and balls are: it is the scene the
human actually sorted against and the scene the renderer draws. Everything
in the "scene geometry" block below is a hand-kept copy of it, the same
manual mirroring `hand_frame.py` already accepts against `hands.ts`. **The
two files must change together** -- if they drift, a demonstration gets
scored against a layout the human never saw, and the failure is silent
because both halves stay internally consistent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from ar_contracts import HumanEpisode, ObjectState

# --------------------------------------------------------- scene geometry --
# Mirrors packages/xr-web/src/sortLayout.ts. See the module docstring.

BallColor = Literal["red", "blue"]

SORT_TASK_ID = "sort_red_blue_balls"

TABLE_Z_M = 0.14
BALL_RADIUS_M = 0.03
BASKET_INTERIOR_M = 0.15
BASKET_WALL_HEIGHT_M = 0.07


@dataclass(frozen=True)
class BasketSpec:
    id: str
    color: BallColor
    # Center of the interior volume's *floor*, not of the volume -- so the
    # containment test reads `z >= center.z`, matching sortTask.ts.
    center: tuple[float, float, float]


@dataclass(frozen=True)
class BallSpec:
    id: str
    color: BallColor


BASKETS: tuple[BasketSpec, ...] = (
    BasketSpec(id="red_basket", color="red", center=(0.26, 0.175, TABLE_Z_M)),
    BasketSpec(id="blue_basket", color="blue", center=(0.26, -0.175, TABLE_Z_M)),
)

# Start positions are deliberately not mirrored: the predicate is about where
# a ball *ended*, and a copy of the start layout would be a second thing to
# keep in sync for no gain.
BALLS: tuple[BallSpec, ...] = (
    BallSpec(id="red_ball_0", color="red"),
    BallSpec(id="blue_ball_0", color="blue"),
    BallSpec(id="blue_ball_1", color="blue"),
    BallSpec(id="red_ball_1", color="red"),
    BallSpec(id="red_ball_2", color="red"),
    BallSpec(id="blue_ball_2", color="blue"),
)


# ------------------------------------------------------------- containment --


def inside_basket(position: tuple[float, float, float], basket: BasketSpec) -> bool:
    """Is this point inside the basket's interior volume?

    Mirrors `insideBasket` in packages/xr-web/src/sortTask.ts exactly,
    including why it is a volume and not a footprint: a ball carried *over* a
    basket on the way somewhere else must not score, so the test is bounded
    above by the wall rim rather than open-ended upward.
    """
    half = BASKET_INTERIOR_M / 2
    x, y, z = position
    cx, cy, cz = basket.center
    if abs(x - cx) > half or abs(y - cy) > half:
        return False
    return cz <= z <= cz + BASKET_WALL_HEIGHT_M


def containing_basket(
    position: tuple[float, float, float],
    baskets: Iterable[BasketSpec] = BASKETS,
) -> BasketSpec | None:
    """Which basket, if any, contains this point."""
    for basket in baskets:
        if inside_basket(position, basket):
            return basket
    return None


# ----------------------------------------------------------- the predicate --


@dataclass(frozen=True)
class SortTaskSpec:
    """What "sorted" means, as data rather than as a hardcoded rule.

    Parameterized the same way `TaskSpec` parameterized the old hardcoded
    cube_to_bin check: a test can build a two-ball scene without the module
    having to know about it, and a future layout change is a value change.
    """

    task_id: str = SORT_TASK_ID
    balls: tuple[BallSpec, ...] = BALLS
    baskets: tuple[BasketSpec, ...] = BASKETS

    def basket_for(self, color: BallColor) -> BasketSpec:
        for basket in self.baskets:
            if basket.color == color:
                return basket
        raise KeyError(f"no basket for color {color}")


SORT_TASK_SPEC = SortTaskSpec()


@dataclass(frozen=True)
class SortTaskResult:
    """Why the sort passed or failed, not just whether.

    The three failure buckets are kept apart because they mean different
    things to whoever reads the verdict: a misplaced ball is a demonstration
    that taught the wrong thing, an unplaced ball is an incomplete one, and a
    missing ball is a *recording* fault -- the episode never carried a pose
    for it at all, which is a capture bug rather than a human error.
    """

    success: bool
    # Ball id -> the basket it ended in, or None if it ended in no basket.
    # Balls with no recorded pose are absent from this mapping entirely
    # rather than mapping to None, so "not placed" and "not recorded" stay
    # distinguishable here too.
    placements: Mapping[str, str | None]
    misplaced: tuple[str, ...]
    unplaced: tuple[str, ...]
    missing: tuple[str, ...]


def final_object_states(states: Iterable[ObjectState]) -> dict[str, ObjectState]:
    """The last recorded pose of each object.

    A HumanEpisode's `object_states` is a time series with one sample per
    object per frame (see `recordFrame` in sortTeleopMain.ts), so "where the
    ball ended up" is a reduction over it, not a lookup.

    `timestamp_ns` decides when both samples carry one, because a recorder is
    free to interleave objects within a frame and a later list position does
    not have to mean a later moment. When either sample is untimestamped --
    the button task's single end-of-episode snapshot, where the field stays
    None -- list order wins, since capture order is then the only ordering
    the record has.
    """
    latest: dict[str, ObjectState] = {}
    for state in states:
        prior = latest.get(state.id)
        if (
            prior is not None
            and prior.timestamp_ns is not None
            and state.timestamp_ns is not None
            and state.timestamp_ns < prior.timestamp_ns
        ):
            continue
        latest[state.id] = state
    return latest


def evaluate_sort_task(
    object_states: Iterable[ObjectState],
    spec: SortTaskSpec = SORT_TASK_SPEC,
) -> SortTaskResult:
    """Score a recorded sort from where its objects finished.

    Deliberately does not consult the episode's event stream. Events say what
    the browser's own physics believed at the time; the object poses say where
    things actually are, and re-deriving containment from the poses is what
    makes this predicate reproducible off-device -- including against poses
    that came out of the simulator rather than the browser.
    """
    latest = final_object_states(object_states)

    placements: dict[str, str | None] = {}
    misplaced: list[str] = []
    unplaced: list[str] = []
    missing: list[str] = []

    for ball in spec.balls:
        state = latest.get(ball.id)
        if state is None:
            missing.append(ball.id)
            continue
        basket = containing_basket(state.position_m, spec.baskets)
        placements[ball.id] = basket.id if basket else None
        if basket is None:
            unplaced.append(ball.id)
        elif basket.color != ball.color:
            misplaced.append(ball.id)

    success = not (misplaced or unplaced or missing)
    return SortTaskResult(
        success=success,
        placements=placements,
        misplaced=tuple(misplaced),
        unplaced=tuple(unplaced),
        missing=tuple(missing),
    )


def evaluate_sort_episode(
    human_episode: HumanEpisode,
    spec: SortTaskSpec = SORT_TASK_SPEC,
) -> SortTaskResult:
    """`evaluate_sort_task` against a recorded episode. Reads only; the raw
    HumanEpisode is never touched (spec section 6's core invariant)."""
    return evaluate_sort_task(human_episode.object_states, spec)
