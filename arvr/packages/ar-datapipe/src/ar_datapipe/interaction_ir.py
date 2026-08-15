"""Interaction IR derivation — Shadow Robot Spatial Demonstration Pipeline
spec section 22-23.

Derives object-relative, robot-independent physical intent from a
HumanEpisode plus the InteractableAsset it was recorded against:

    T_object_hand = inverse(T_world_object) x T_world_hand

Pure numpy quaternion math, not Pinocchio's `pin.SE3` -- this module should
import cleanly everywhere (Windows included) per the workspace's lazy-import
convention, and genuinely doesn't need Pinocchio for a rigid-transform
compose/invert.

Two derivations live here, both producing the same `InteractionIR` shape:

* `derive_interaction_ir` -- one IR from an episode plus the articulated
  asset it was recorded against (the button task). The asset supplies the
  geometry; the episode only dates the contact.
* `derive_sort_interaction_ir` -- one IR per manipulated object, from the
  episode alone (the ball-sorting task). There is no articulated asset to
  consult: a ball has no parts, no travel and no axis, and everything the IR
  can say about it has to come out of the recorded event stream and object
  poses.

Never mutates `human_episode` -- the acceptance gate for this phase is
literally "raw HumanEpisode unchanged" (spec section 6's core invariant,
re-checked here specifically because this is the first stage that reads a
HumanEpisode after it's recorded).
"""

from __future__ import annotations

import math

import numpy as np
from ar_contracts import (
    HumanEpisode,
    HumanEpisodeEvent,
    InteractableAsset,
    InteractionIR,
    InteractionPhase,
    ObjectState,
    OrientationXYZW,
    Pose,
)

# Judgment call, not derived from a formula the spec gives (it shows one
# illustrative example, section 22, with no stated relationship between its
# numbers): how far "above" the press point (along the negative press axis)
# the approach phase's target sits, and how far the retract phase backs off
# afterward. Same order of magnitude as the spec's own example.
APPROACH_HOVER_M = 0.02
RETRACT_DISTANCE_M = 0.04


def _quat_conjugate(q: OrientationXYZW) -> np.ndarray:
    x, y, z, w = q
    return np.array([-x, -y, -z, w])


def _quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product a (x) b, xyzw convention."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ]
    )


def _rotate_vector(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate v by unit quaternion q (xyzw) -- v + 2*cross(qxyz, cross(qxyz, v) + w*v)."""
    qxyz, w = q[:3], q[3]
    t = 2.0 * np.cross(qxyz, v)
    return v + w * t + np.cross(qxyz, t)


def object_relative_pose(world_object: Pose, world_hand: Pose) -> tuple[np.ndarray, np.ndarray]:
    """T_object_hand = inverse(T_world_object) x T_world_hand.

    Returns (position, orientation_xyzw) as raw numpy arrays -- callers that
    need a validated ar_contracts.Pose should wrap the result themselves;
    this function is the pure math, kept separate so it's testable against
    a hand-computed expectation without contract-validation noise.
    """
    q_hand = np.array(world_hand.orientation_xyzw)
    t_object = np.array(world_object.position_m)
    t_hand = np.array(world_hand.position_m)

    q_object_inv = _quat_conjugate(world_object.orientation_xyzw)
    position = _rotate_vector(q_object_inv, t_hand - t_object)
    orientation = _quat_multiply(q_object_inv, q_hand)
    orientation = orientation / np.linalg.norm(orientation)
    return position, orientation


def derive_interaction_ir(
    human_episode: HumanEpisode,
    asset: InteractableAsset,
    asset_world_pose: Pose,
) -> InteractionIR:
    part_name = next(iter(asset.parts))
    part = asset.parts[part_name]
    local_origin_m = part.local_origin_m or (0.0, 0.0, 0.0)
    axis = part.axis or (0.0, 0.0, -1.0)

    contact_events = [e for e in human_episode.events if e.type == "contact"]
    contact_timestamp_ns = contact_events[0].timestamp_ns if contact_events else None

    hover_offset = tuple(-a * APPROACH_HOVER_M for a in axis)
    approach_target = tuple(o + h for o, h in zip(local_origin_m, hover_offset, strict=True))

    phases: list[InteractionPhase] = [
        InteractionPhase(type="approach", target_position_m=approach_target)
    ]
    if contact_timestamp_ns is not None:
        phases.append(InteractionPhase(type="contact"))
    if part.interaction == "press" and part.travel_m is not None:
        phases.append(InteractionPhase(type="press", axis=axis, distance_m=part.travel_m))
    elif part.interaction == "pull" and part.limit_m is not None:
        pull_distance = part.limit_m[1] - part.limit_m[0]
        phases.append(InteractionPhase(type="pull", axis=axis, distance_m=pull_distance))
    elif part.interaction == "grasp":
        phases.append(InteractionPhase(type="grasp"))
    phases.append(InteractionPhase(type="retract", distance_m=RETRACT_DISTANCE_M))

    return InteractionIR(
        task_id=human_episode.metadata.task_id,
        asset_id=asset.asset_id,
        reference_frame=f"{asset.asset_id}.{part_name}",
        phases=tuple(phases),
    )


# ------------------------------------------- pick-and-place (ball sorting) --

# How far a grasped object must rise above where it was picked up before the
# motion counts as a lift, and how far it must travel horizontally before the
# carry counts as a transport. Both are thresholds on *recorded* motion, not
# targets: their job is to separate a real carry from the centimetre of
# wobble a tracked hand puts into a held object while the human is still
# closing their fingers. 1cm is a third of the sorting task's ball radius,
# and 2cm is well under the ~7cm that separates two neighbouring balls, so
# neither can fire on noise or miss a genuine pick-and-place.
LIFT_RISE_M = 0.01
TRANSPORT_TRAVEL_M = 0.02


def _timed_samples(human_episode: HumanEpisode, object_id: str) -> list[ObjectState]:
    """This object's pose time series, in time order.

    Untimestamped samples are dropped rather than assumed to be in capture
    order: `ObjectState.timestamp_ns` is optional precisely because the
    button task records one end-of-episode snapshot, and a snapshot cannot be
    lined up against a grasp. Sorting is stable, so samples that share a
    timestamp keep the order they were recorded in.
    """
    samples = [
        state
        for state in human_episode.object_states
        if state.id == object_id and state.timestamp_ns is not None
    ]
    return sorted(samples, key=_ts)


def _ts(state: ObjectState) -> int:
    """The timestamp `_timed_samples` already guaranteed this sample has.

    Exists so the derivation below is not littered with `or 0` fallbacks for
    a None that cannot reach it.
    """
    return state.timestamp_ns or 0


def _sample_at(samples: list[ObjectState], timestamp_ns: int) -> ObjectState | None:
    """The object's pose as of a moment: the last sample at or before it.

    Falls forward to the earliest later sample when the moment precedes the
    whole series, because an event timestamped one frame ahead of the first
    recorded pose is a capture-order artifact, not a missing pose.
    """
    at_or_before = [s for s in samples if _ts(s) <= timestamp_ns]
    if at_or_before:
        return at_or_before[-1]
    return samples[0] if samples else None


def _earliest_recorded_ns(human_episode: HumanEpisode) -> int | None:
    """When this episode's record actually starts.

    Used only to date the first approach. Hand frames and object states are
    both consulted because an episode may legitimately carry one without the
    other -- a record with no hand frames is still a record of the objects.
    """
    candidates = [frame.timestamp_ns for frame in human_episode.hand_frames]
    candidates += [
        state.timestamp_ns
        for state in human_episode.object_states
        if state.timestamp_ns is not None
    ]
    return min(candidates) if candidates else None


def derive_sort_interaction_ir(human_episode: HumanEpisode) -> tuple[InteractionIR, ...]:
    """One InteractionIR per object the human actually picked up.

    Phases are evidence, not a template. Every one of them is dated by
    something in the record -- `grasp_start` opens a grasp, the object rising
    opens a lift, horizontal travel opens a transport, `grasp_end` opens a
    release, `ball_enter_basket` opens a place -- and a phase the record
    cannot date is simply not emitted. An episode whose hand tracking dropped
    mid-carry therefore yields a short IR rather than a plausible-looking
    invented one, which is the whole point: a downstream gate can tell the
    difference, and could not if the gaps were filled in.

    Phases come out in timestamp order, which for this recorder puts `place`
    *after* `release`: the browser scores a ball only once it is out of the
    hand and settled (see `settle` in sortTask.ts, which refuses to count a
    held ball). Reordering them into the textbook sequence would make the
    timestamps non-monotonic and the IR unreplayable, so the record wins.

    Only `ball_enter_basket` opens a place, never `wrong_basket`. A ball that
    landed in the wrong basket is a recorded fact -- it stays in the episode's
    event stream either way -- but it is not demonstrated intent, and an IR is
    a statement of intent. If the human then picked it back out and placed it
    correctly, that second grasp cycle supplies the place.

    Not restricted to `sort_red_blue_balls`: nothing below knows about balls
    or baskets, so any grasp-based episode derives correctly, and an episode
    with no grasp events (the button task) correctly derives nothing.

    Never mutates `human_episode`.
    """
    # Stable sort, so events sharing a timestamp -- a release and the landing
    # it caused can be recorded in the same frame -- keep their recorded order.
    events = sorted(human_episode.events, key=lambda e: e.timestamp_ns)
    grasp_starts = [e for e in events if e.type == "grasp_start" and e.object_id]
    if not grasp_starts:
        return ()

    episode_start_ns = _earliest_recorded_ns(human_episode)
    metadata = human_episode.metadata

    # Ordered by when each object was first picked up, so the IRs read in the
    # order the human worked; object id breaks ties so two objects grasped in
    # the same frame still come out deterministically.
    first_grasp: dict[str, int] = {}
    for event in grasp_starts:
        first_grasp.setdefault(str(event.object_id), event.timestamp_ns)

    irs: list[InteractionIR] = []
    for object_id in sorted(first_grasp, key=lambda oid: (first_grasp[oid], oid)):
        samples = _timed_samples(human_episode, object_id)
        phases: list[InteractionPhase] = []

        for grasp in (e for e in grasp_starts if e.object_id == object_id):
            phases.extend(
                _grasp_cycle_phases(
                    events=events,
                    samples=samples,
                    object_id=object_id,
                    grasp_ns=grasp.timestamp_ns,
                    episode_start_ns=episode_start_ns,
                )
            )

        phases.sort(key=lambda phase: phase.timestamp_ns or 0)
        irs.append(
            InteractionIR(
                task_id=metadata.task_id,
                asset_id=metadata.asset_id,
                # World, not object-relative -- unlike the button IR. The
                # manipulated object is the thing that moves, so expressing
                # its trajectory relative to itself would be a run of zeros.
                # The recorded poses are already in the episode's own frame.
                reference_frame=metadata.coordinate_frame,
                phases=tuple(phases),
            )
        )
    return tuple(irs)


def _grasp_cycle_phases(
    *,
    events: list[HumanEpisodeEvent],
    samples: list[ObjectState],
    object_id: str,
    grasp_ns: int,
    episode_start_ns: int | None,
) -> list[InteractionPhase]:
    """The phases of one pick-up-and-put-down of one object.

    Split out of `derive_sort_interaction_ir` because a ball can be grasped
    more than once in an episode -- dropped and retried, or pulled back out
    of the wrong basket -- and each attempt is its own approach/grasp/.../
    release run rather than an extra phase bolted onto the first.
    """
    release_ns = _next_event_ns(events, object_id, "grasp_end", after_ns=grasp_ns)
    next_grasp_ns = _next_event_ns(events, object_id, "grasp_start", after_ns=grasp_ns)
    hold_end_ns = release_ns if release_ns is not None else next_grasp_ns

    grasp_sample = _sample_at(samples, grasp_ns)
    phases: list[InteractionPhase] = []

    # The reach: it begins the moment the hand became free for this object --
    # the previous release, or the start of the record for the first pick --
    # and it ends where the object was when the hand closed on it. Both ends
    # are recorded facts. A grasp on the very first recorded frame has no
    # reach interval and so gets no approach phase.
    reach_from_ns = _previous_release_ns(events, before_ns=grasp_ns)
    if reach_from_ns is None:
        reach_from_ns = episode_start_ns
    if reach_from_ns is not None and reach_from_ns < grasp_ns:
        phases.append(
            InteractionPhase(
                type="approach",
                target_position_m=_position_of(grasp_sample),
                timestamp_ns=reach_from_ns,
            )
        )

    phases.append(
        InteractionPhase(
            type="grasp",
            target_position_m=_position_of(grasp_sample),
            timestamp_ns=grasp_ns,
        )
    )

    held = [
        s
        for s in samples
        if _ts(s) >= grasp_ns and (hold_end_ns is None or _ts(s) <= hold_end_ns)
    ]
    if grasp_sample is not None and held:
        grasp_position = grasp_sample.position_m

        rose = [s for s in held if s.position_m[2] > grasp_position[2] + LIFT_RISE_M]
        if rose:
            apex = max(s.position_m[2] for s in held)
            phases.append(
                InteractionPhase(
                    type="lift",
                    target_position_m=rose[0].position_m,
                    axis=(0.0, 0.0, 1.0),
                    # How far the object actually came up, which is the part a
                    # different robot has to reproduce; the absolute heights
                    # belong to this table, the rise does not.
                    distance_m=apex - grasp_position[2],
                    timestamp_ns=rose[0].timestamp_ns,
                )
            )

        travelled = [
            s
            for s in held
            if math.hypot(
                s.position_m[0] - grasp_position[0], s.position_m[1] - grasp_position[1]
            )
            > TRANSPORT_TRAVEL_M
        ]
        if travelled:
            phases.append(
                InteractionPhase(
                    type="transport",
                    # Where the carry ended, not where it was when it started
                    # moving -- a transport is named by its destination.
                    target_position_m=held[-1].position_m,
                    timestamp_ns=travelled[0].timestamp_ns,
                )
            )

    if release_ns is not None:
        phases.append(
            InteractionPhase(
                type="release",
                target_position_m=_position_of(_sample_at(samples, release_ns)),
                timestamp_ns=release_ns,
            )
        )

    # The landing has to belong to *this* grasp cycle: a ball picked back out
    # of a basket and re-placed would otherwise credit both cycles with the
    # first landing.
    place = _first_event(
        events,
        object_id,
        "ball_enter_basket",
        after_ns=grasp_ns,
        before_ns=next_grasp_ns,
    )
    if place is not None:
        phases.append(
            InteractionPhase(
                type="place",
                target_position_m=_position_of(_sample_at(samples, place.timestamp_ns)),
                timestamp_ns=place.timestamp_ns,
            )
        )
    return phases


def _position_of(state: ObjectState | None) -> tuple[float, float, float] | None:
    return state.position_m if state is not None else None


def _first_event(
    events: list[HumanEpisodeEvent],
    object_id: str,
    event_type: str,
    *,
    after_ns: int,
    before_ns: int | None = None,
) -> HumanEpisodeEvent | None:
    for event in events:
        if event.object_id != object_id or event.type != event_type:
            continue
        if event.timestamp_ns < after_ns:
            continue
        if before_ns is not None and event.timestamp_ns >= before_ns:
            continue
        return event
    return None


def _next_event_ns(
    events: list[HumanEpisodeEvent], object_id: str, event_type: str, *, after_ns: int
) -> int | None:
    """The next event of this type for this object strictly after a moment.

    Strictly after, so a grasp cannot pair with itself when a recorder stamps
    two events in the same frame.
    """
    for event in events:
        if event.object_id != object_id or event.type != event_type:
            continue
        if event.timestamp_ns > after_ns:
            return event.timestamp_ns
    return None


def _previous_release_ns(events: list[HumanEpisodeEvent], *, before_ns: int) -> int | None:
    """When the hand last let go of anything before this moment."""
    releases = [
        e.timestamp_ns
        for e in events
        if e.type == "grasp_end" and e.timestamp_ns < before_ns
    ]
    return max(releases) if releases else None
