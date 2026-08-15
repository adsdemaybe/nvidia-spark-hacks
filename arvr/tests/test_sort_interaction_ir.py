"""Interaction IR derivation for pick-and-place (the ball-sorting task).

The rule under test throughout: a phase must be evidenced by something in the
record. These tests spend most of their effort on the cases where the
evidence is *missing* -- a grasp with no release, a record with no object
poses, a landing in the wrong basket -- because that is where a derivation is
tempted to invent a plausible phase, and an invented phase is indistinguishable
from a real one once it is in the IR.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from ar_contracts import (
    HandFrame,
    HumanEpisode,
    HumanEpisodeEvent,
    HumanEpisodeMetadata,
    ObjectState,
)
from ar_datapipe import derive_sort_interaction_ir
from ar_datapipe.sort_task import (
    BASKETS,
    SORT_TASK_ID,
    TABLE_Z_M,
    containing_basket,
    inside_basket,
)

STEP_NS = 10_000_000  # 10 ms, one frame at 100 Hz

RED_BASKET = next(b for b in BASKETS if b.id == "red_basket")
BLUE_BASKET = next(b for b in BASKETS if b.id == "blue_basket")

RED_START = (0.17, 0.045, TABLE_Z_M + 0.03)
BLUE_START = (0.17, -0.045, TABLE_Z_M + 0.03)
CARRY_Z = 0.25


def _rest_in(basket) -> tuple[float, float, float]:
    return (basket.center[0], basket.center[1], TABLE_Z_M + 0.03)


def _carry_track(start, basket) -> list[tuple[float, float, float]]:
    """One complete pick-up-carry-drop, one position per frame.

    Frame 3 is where the hand closes, 9 is where it opens, 12 is where the
    ball has settled on the basket floor -- the fall between 9 and 12 is why
    the landing is recorded after the release, not before it.
    """
    bx, by, _ = basket.center
    over = _rest_in(basket)
    return [
        start,  # 0
        start,  # 1
        start,  # 2
        start,  # 3 grasp closes here
        (start[0], start[1], 0.20),  # 4 rising
        (start[0], start[1], CARRY_Z),  # 5
        (0.20, (start[1] + by) / 2, CARRY_Z),  # 6 first real horizontal travel
        (0.23, by, CARRY_Z),  # 7
        (bx, by, CARRY_Z),  # 8
        (bx, by, CARRY_Z),  # 9 hand opens here
        (bx, by, 0.20),  # 10 falling
        over,  # 11
        over,  # 12 settled, scored here
    ]


def _track_states(object_id: str, positions, start_frame: int = 0) -> list[ObjectState]:
    return [
        ObjectState(
            id=object_id,
            position_m=position,
            timestamp_ns=(start_frame + i) * STEP_NS,
        )
        for i, position in enumerate(positions)
    ]


def _event(event_type: str, frame: int, object_id=None, container_id=None):
    return HumanEpisodeEvent(
        type=event_type,
        timestamp_ns=frame * STEP_NS,
        object_id=object_id,
        container_id=container_id,
    )


def _episode(
    object_states=(),
    events=(),
    hand_frames=(),
    task_id: str = SORT_TASK_ID,
) -> HumanEpisode:
    return HumanEpisode(
        metadata=HumanEpisodeMetadata(
            episode_id=str(uuid.uuid4()),
            task_id=task_id,
            asset_id="sort_scene_01",
            hand_provider="mock",
        ),
        hand_frames=list(hand_frames),
        object_states=list(object_states),
        events=list(events),
    )


def _one_ball_episode() -> HumanEpisode:
    """red_ball_0 picked off the table and dropped in the red basket."""
    return _episode(
        object_states=_track_states("red_ball_0", _carry_track(RED_START, RED_BASKET)),
        events=[
            _event("task_start", 0),
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
            _event("ball_enter_basket", 12, object_id="red_ball_0", container_id="red_basket"),
            _event("task_finish", 13),
        ],
    )


def _phase(ir, phase_type):
    return next(p for p in ir.phases if p.type == phase_type)


# ---------------------------------------------------------------------------
# the full, well-formed carry
# ---------------------------------------------------------------------------


def test_a_full_carry_derives_every_phase_it_can_evidence():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    assert [p.type for p in ir.phases] == [
        "approach",
        "grasp",
        "lift",
        "transport",
        "release",
        "place",
    ]


def test_place_lands_after_release_because_a_held_ball_is_never_scored():
    # Not a bug in the ordering: the browser only scores a ball once it is out
    # of the hand and settled, so the record genuinely has the landing after
    # the release. Timestamps have to stay monotonic for the IR to replay.
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    timestamps = [p.timestamp_ns for p in ir.phases]
    assert timestamps == sorted(timestamps)
    assert _phase(ir, "place").timestamp_ns > _phase(ir, "release").timestamp_ns


def test_phases_are_dated_by_the_events_and_samples_that_evidence_them():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    assert _phase(ir, "approach").timestamp_ns == 0
    assert _phase(ir, "grasp").timestamp_ns == 3 * STEP_NS
    assert _phase(ir, "lift").timestamp_ns == 4 * STEP_NS
    assert _phase(ir, "transport").timestamp_ns == 6 * STEP_NS
    assert _phase(ir, "release").timestamp_ns == 9 * STEP_NS
    assert _phase(ir, "place").timestamp_ns == 12 * STEP_NS


def test_approach_and_grasp_target_where_the_ball_was_picked_up():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    assert _phase(ir, "grasp").target_position_m == RED_START
    assert _phase(ir, "approach").target_position_m == RED_START


def test_lift_records_the_height_the_ball_actually_gained():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    lift = _phase(ir, "lift")
    assert lift.axis == (0.0, 0.0, 1.0)
    assert lift.distance_m == pytest.approx(CARRY_Z - RED_START[2])


def test_transport_targets_where_the_carry_ended():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    assert _phase(ir, "transport").target_position_m == (
        RED_BASKET.center[0],
        RED_BASKET.center[1],
        CARRY_Z,
    )


def test_place_targets_a_point_actually_inside_the_basket():
    # Cross-check against the predicate: the IR's place target has to satisfy
    # the same containment rule the task is scored by, or the IR is describing
    # a placement the predicate would reject.
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    target = _phase(ir, "place").target_position_m
    assert target is not None
    assert inside_basket(target, RED_BASKET)
    assert containing_basket(target) is RED_BASKET


def test_ir_identity_comes_from_the_episode_not_from_an_asset():
    (ir,) = derive_sort_interaction_ir(_one_ball_episode())
    assert ir.task_id == SORT_TASK_ID
    assert ir.asset_id == "sort_scene_01"
    # World frame, unlike the button IR: the manipulated object is the thing
    # that moves, so its own frame would make the trajectory a run of zeros.
    assert ir.reference_frame == "struct_world"


def test_derivation_never_mutates_the_source_human_episode():
    episode = _one_ball_episode()
    before = copy.deepcopy(episode)
    derive_sort_interaction_ir(episode)
    assert episode.metadata == before.metadata
    assert episode.hand_frames == before.hand_frames
    assert episode.object_states == before.object_states
    assert episode.events == before.events


# ---------------------------------------------------------------------------
# several balls
# ---------------------------------------------------------------------------


def _two_ball_episode() -> HumanEpisode:
    """blue first, then red -- so "in grasp order" is visibly not "in id order"."""
    return _episode(
        object_states=[
            *_track_states("blue_ball_0", _carry_track(BLUE_START, BLUE_BASKET)),
            *_track_states("red_ball_0", _carry_track(RED_START, RED_BASKET), start_frame=20),
        ],
        events=[
            _event("grasp_start", 3, object_id="blue_ball_0"),
            _event("grasp_end", 9, object_id="blue_ball_0"),
            _event(
                "ball_enter_basket", 12, object_id="blue_ball_0", container_id="blue_basket"
            ),
            _event("grasp_start", 23, object_id="red_ball_0"),
            _event("grasp_end", 29, object_id="red_ball_0"),
            _event("ball_enter_basket", 32, object_id="red_ball_0", container_id="red_basket"),
        ],
    )


def test_one_ir_per_manipulated_ball_in_the_order_they_were_worked():
    irs = derive_sort_interaction_ir(_two_ball_episode())
    assert len(irs) == 2
    firsts = [ir.phases[0].timestamp_ns for ir in irs]
    assert firsts == sorted(firsts)
    assert _phase(irs[0], "grasp").timestamp_ns == 3 * STEP_NS
    assert _phase(irs[1], "grasp").timestamp_ns == 23 * STEP_NS


def test_a_later_approach_starts_when_the_hand_became_free():
    # Not at the start of the episode -- the hand was busy with the first ball
    # until it let go, and that release is the only dated moment the record
    # offers for when the reach for the second ball could have begun.
    _blue, red = derive_sort_interaction_ir(_two_ball_episode())
    assert _phase(red, "approach").timestamp_ns == 9 * STEP_NS


# ---------------------------------------------------------------------------
# incomplete and irregular records
# ---------------------------------------------------------------------------


def test_an_episode_with_no_grasps_derives_nothing():
    # The button task's event vocabulary produces no pick-and-place IR, rather
    # than an empty-phased one that looks like a failed sort.
    episode = _episode(events=[_event("contact", 5)], task_id="press_button")
    assert derive_sort_interaction_ir(episode) == ()


def test_events_alone_derive_only_what_events_can_evidence():
    # No object poses at all: grasp, release and place are each dated by an
    # event, but nothing says where the ball was, and nothing says the ball
    # rose or travelled -- so there is no approach, no lift and no transport.
    episode = _episode(
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
            _event("ball_enter_basket", 12, object_id="red_ball_0", container_id="red_basket"),
        ]
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert [p.type for p in ir.phases] == ["grasp", "release", "place"]
    assert all(p.target_position_m is None for p in ir.phases)


def test_untimestamped_object_states_cannot_evidence_a_lift():
    # A single end-of-episode snapshot (the button task's shape) cannot be
    # lined up against a grasp, so it must not be read as motion.
    episode = _episode(
        object_states=[ObjectState(id="red_ball_0", position_m=_rest_in(RED_BASKET))],
        hand_frames=[HandFrame(timestamp_ns=0, source_device="mock", hand="right")],
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert [p.type for p in ir.phases] == ["approach", "grasp", "release"]


def test_a_grasp_that_never_ends_yields_no_release_and_no_place():
    # Hand tracking dropped mid-carry. The IR gets shorter; it does not get a
    # guessed release at the end of the record.
    positions = _carry_track(RED_START, RED_BASKET)[:8]
    episode = _episode(
        object_states=_track_states("red_ball_0", positions),
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("tracking_lost", 7),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert [p.type for p in ir.phases] == ["approach", "grasp", "lift", "transport"]


def test_a_ball_that_never_left_the_table_has_no_lift_or_transport():
    still = [RED_START] * 10
    episode = _episode(
        object_states=_track_states("red_ball_0", still),
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 6, object_id="red_ball_0"),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert [p.type for p in ir.phases] == ["approach", "grasp", "release"]


def test_wrong_basket_does_not_open_a_place_phase():
    # A red ball dropped in the blue basket is a recorded fact -- it stays in
    # the episode's events -- but it is not demonstrated intent, and an IR is
    # a statement of intent.
    episode = _episode(
        object_states=_track_states("red_ball_0", _carry_track(RED_START, BLUE_BASKET)),
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
            _event("wrong_basket", 12, object_id="red_ball_0", container_id="blue_basket"),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert "place" not in [p.type for p in ir.phases]
    assert [p.type for p in ir.phases] == ["approach", "grasp", "lift", "transport", "release"]


def test_a_reclaimed_ball_gets_two_cycles_and_one_place():
    # Dropped in the wrong basket, fished back out, put right. Both attempts
    # are in the IR; only the corrected one is a place, and the first cycle
    # must not borrow the second cycle's landing.
    episode = _episode(
        object_states=[
            *_track_states("red_ball_0", _carry_track(RED_START, BLUE_BASKET)),
            *_track_states(
                "red_ball_0", _carry_track(_rest_in(BLUE_BASKET), RED_BASKET), start_frame=20
            ),
        ],
        events=[
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
            _event("wrong_basket", 12, object_id="red_ball_0", container_id="blue_basket"),
            _event("grasp_start", 23, object_id="red_ball_0"),
            _event("grasp_end", 29, object_id="red_ball_0"),
            _event("ball_enter_basket", 32, object_id="red_ball_0", container_id="red_basket"),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    types = [p.type for p in ir.phases]
    assert types.count("grasp") == 2
    assert types.count("release") == 2
    assert types.count("place") == 1
    assert _phase(ir, "place").timestamp_ns == 32 * STEP_NS
    timestamps = [p.timestamp_ns for p in ir.phases]
    assert timestamps == sorted(timestamps)


def test_a_grasp_on_the_first_recorded_frame_has_no_approach():
    # There is no interval to reach during, so there is nothing to date an
    # approach with.
    episode = _episode(
        object_states=_track_states("red_ball_0", _carry_track(RED_START, RED_BASKET)),
        events=[
            _event("grasp_start", 0, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert "approach" not in [p.type for p in ir.phases]


def test_hand_only_events_are_ignored_rather_than_attributed_to_a_ball():
    # tracking_lost and task_start carry no object_id; they must not become a
    # phase of whichever ball happens to be nearby in the stream.
    episode = _episode(
        object_states=_track_states("red_ball_0", _carry_track(RED_START, RED_BASKET)),
        events=[
            _event("task_start", 0),
            _event("tracking_lost", 2),
            _event("grasp_start", 3, object_id="red_ball_0"),
            _event("grasp_end", 9, object_id="red_ball_0"),
            _event("ball_enter_basket", 12, object_id="red_ball_0", container_id="red_basket"),
            _event("sort_complete", 13),
            _event("task_finish", 13),
        ],
    )
    (ir,) = derive_sort_interaction_ir(episode)
    assert len(ir.phases) == 6


def test_events_recorded_out_of_order_are_still_read_in_time_order():
    # The recorder appends per frame, but nothing in the contract guarantees
    # it, and a derivation that trusted list order would pair the wrong
    # grasp with the wrong release.
    episode = _one_ball_episode()
    shuffled = _episode(
        object_states=episode.object_states,
        events=list(reversed(episode.events)),
    )
    (ir,) = derive_sort_interaction_ir(shuffled)
    assert [p.type for p in ir.phases] == [
        "approach",
        "grasp",
        "lift",
        "transport",
        "release",
        "place",
    ]
