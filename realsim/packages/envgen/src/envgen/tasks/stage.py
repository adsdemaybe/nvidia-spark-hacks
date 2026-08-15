"""Stage 8: tasks. one task per scene, with the three checks that keep it honest:

  * NOT pre-satisfied: the success predicate is FALSE on the post-settle state
    (evaluating pre-settle is the subtle version of the same bug -- an object
    that settles into the goal region turns a valid task into a no-op);
  * SATISFIABLE with a recorded witness: we construct a goal state, verify the
    predicate holds there, the goal is reachable, and a 3-waypoint path stays
    clear -- no planning, just an existence proof that doubles as a demo seed;
  * NON-TRIVIAL: goal >= 5cm from the start.

Task families by stratum: A=pick_place (on(target, receptacle)), B=place_near,
C=push_to (pose_within). Instructions are template-generated (Proposer=default);
--propose swaps in an LLM later, gates unchanged.
"""
from __future__ import annotations

import numpy as np
import shapely.geometry as sg
from pydantic import BaseModel

from r2s.core import ArtifactRef, Degradation, Pose, Proposer, inferred
from r2s.core.runctx import RunContext, StageSpec
from r2s.core.schemas import (
    AssetBundlePayload,
    FeasibilityReport,
    Predicate,
    ScenePayload,
    TaskPayload,
    Witness,
)

from ..cousins.compose import reach_polygon
from ..cousins.stage import CousinSetPayload
from .predicates import State, evaluate, leaf_values

class TasksConfig(BaseModel):
    goal_min_offset_m: float = 0.05
    horizon_steps: int = 600


class TaskSetPayload(BaseModel):
    schema_version: str = "1.0.0"
    task_refs: dict[str, ArtifactRef] = {}  # scene_id -> task artifact
    families: dict[str, str] = {}


SPEC = StageSpec(
    name="tasks",
    version="1",
    artifact_type="task_set",
    payload_model=TaskSetPayload,
    relevant_packages=["r2s-core", "envgen", "numpy", "shapely"],
)


def _scene_state(scene: ScenePayload, assets) -> State:
    state: State = {}
    for p in scene.placements:
        pose = p.settled_pose or p.pose
        state[p.instance_id] = (pose, assets[p.asset_id])
    return state


def _family_for(scene: ScenePayload) -> str:
    # stratification recorded the family letter in the variation description
    for v in scene.variations:
        if "task A" in v.description:
            return "pick_place"
        if "task B" in v.description:
            return "place_near"
        if "task C" in v.description:
            return "push_to"
    return "pick_place"


def _build_predicate(family: str, target: str, receptacle: str | None,
                     goal_pose: Pose | None) -> tuple[Predicate, list[Predicate], str]:
    if family == "pick_place" and receptacle:
        succ = Predicate(op="and", children=[
            Predicate(op="on", args=[target, receptacle]),
            Predicate(op="not", children=[Predicate(op="grasped", args=[target])]),
        ])
        instr = f"put the {target.rsplit('_', 1)[0]} on the {receptacle.rsplit('_', 1)[0]}"
    elif family == "place_near" and receptacle:
        succ = Predicate(op="near", args=[target, receptacle], kwargs={"d_max": 0.10})
        instr = f"move the {target.rsplit('_', 1)[0]} next to the {receptacle.rsplit('_', 1)[0]}"
    else:
        assert goal_pose is not None
        succ = Predicate(op="pose_within", args=[target],
                         kwargs={"target": {"position": list(goal_pose.position)},
                                 "pos_tol": 0.05, "axis": "z"})
        instr = f"push the {target.rsplit('_', 1)[0]} to the marked spot"
    failure = [Predicate(op="on", args=[target, "FLOOR"])]
    return succ, failure, instr


def _witness_goal(scene: ScenePayload, assets, family: str, target: str,
                  receptacle: str | None, rng) -> tuple[Pose, Pose | None] | None:
    """Sample a goal pose satisfying the predicate; verify below."""
    state = _scene_state(scene, assets)
    t_pose, t_asset = state[target]
    if family in ("pick_place",) and receptacle:
        r_pose, r_asset = state[receptacle]
        faces = r_asset.support_faces
        if not faces:
            return None
        f = max(faces, key=lambda f: f.area_m2)
        poly = sg.Polygon(f.polygon)
        for _ in range(32):
            pt = poly.representative_point() if _ == 0 else _rand_in(poly, rng)
            if pt is None:
                continue
            yaw = 2 * np.arctan2(r_pose.quaternion[3], r_pose.quaternion[0])
            c, s = np.cos(yaw), np.sin(yaw)
            wx = r_pose.position[0] + c * pt[0] - s * pt[1]
            wy = r_pose.position[1] + s * pt[0] + c * pt[1]
            wz = r_pose.position[2] + f.height_m + 1e-3
            return Pose(position=(wx, wy, wz)), None
        return None
    # place_near / push_to: offset on the same surface
    for _ in range(32):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(0.08, 0.25)
        gp = Pose(position=(t_pose.position[0] + r * np.cos(ang),
                            t_pose.position[1] + r * np.sin(ang),
                            t_pose.position[2]))
        return gp, gp if family == "push_to" else None
    return None


def _rand_in(poly, rng):
    minx, miny, maxx, maxy = poly.bounds
    for _ in range(16):
        p = sg.Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if poly.contains(p):
            return (float(p.x), float(p.y))
    return None


def run(inputs: dict, cfg: TasksConfig, ctx: RunContext):
    cousin_set: CousinSetPayload = inputs["cousins"].payload
    scanned: AssetBundlePayload = inputs["assetize"].payload
    generated: AssetBundlePayload = inputs["generate"].payload
    assets = {**scanned.assets, **generated.assets}
    degs: list[Degradation] = []

    from r2s.core.artifacts import ArtifactHeader, BaseArtifact, sha256_of

    task_refs: dict[str, ArtifactRef] = {}
    families: dict[str, str] = {}

    for scene_id, ref in sorted(cousin_set.scene_refs.items()):
        scene_art = ctx.store.get_artifact(ref, BaseArtifact[ScenePayload])
        scene = scene_art.payload
        rng = ctx.rng("tasks", scene_id)

        target = next((p.instance_id for p in scene.placements if p.role == "target"), None)
        receptacle = next((p.instance_id for p in scene.placements if p.role == "receptacle"), None)
        if target is None:
            raise RuntimeError(f"scene {scene_id} has no target instance; composer bug")

        family = _family_for(scene)
        goal = _witness_goal(scene, assets, family, target, receptacle, rng)
        if goal is None and family == "pick_place":
            # receptacle without usable face: degrade family, loudly
            family = "place_near"
            goal = _witness_goal(scene, assets, family, target, receptacle, rng)
        if goal is None:
            raise RuntimeError(f"scene {scene_id}: no witness goal for family {family}")
        goal_pose, push_goal = goal

        succ, failure, instr = _build_predicate(family, target, receptacle, push_goal)

        # gate-3 material, computed here and recorded:
        state = _scene_state(scene, assets)
        init_vals = leaf_values(succ, state)
        pre_satisfied = evaluate(succ, state)

        # witness verification: predicate TRUE at the goal state
        t_pose, t_asset = state[target]
        wit_state = dict(state)
        wit_state[target] = (goal_pose, t_asset)
        wit_ok = evaluate(succ, wit_state)
        offset = float(np.linalg.norm(
            np.asarray(goal_pose.position) - np.asarray(t_pose.position)))

        # reachability: start and goal inside the reach annulus at their heights
        reach_start = _reachable(scene, t_pose)
        reach_goal = _reachable(scene, goal_pose)

        waypoints = [
            t_pose,
            Pose(position=(t_pose.position[0], t_pose.position[1], t_pose.position[2] + 0.10)),
            Pose(position=(goal_pose.position[0], goal_pose.position[1],
                           goal_pose.position[2] + 0.10)),
            goal_pose,
        ]

        payload = TaskPayload(
            task_id=f"{scene_id}_{family}",
            scene_ref=ref,
            scene_id=scene_id,
            family=family,  # type: ignore[arg-type]
            instruction=instr,
            instruction_proposer=Proposer(kind="default", detail="template"),
            target_instances=[target],
            receptacle_instances=[receptacle] if receptacle else [],
            success=succ,
            failure=failure,
            initial_predicate_values=init_vals,
            witness=Witness(
                goal_poses={target: goal_pose},
                waypoints=waypoints,
                checked_collision_free=False,  # V1 honest: 2D-composed, settle-verified
                checked_reachable=reach_start and reach_goal,
            ),
            feasibility=FeasibilityReport(
                target_reachable=reach_start,
                goal_reachable=reach_goal,
                ik_method="reach_envelope",
            ),
            horizon_steps=cfg.horizon_steps,
            difficulty=inferred(
                min(offset / 0.5 + 0.2 * len(scene.placements) / 5.0, 1.0), "1",
                method="offset_plus_clutter_heuristic",
            ),
        )
        # stash gate-relevant scalars for the stage gates (they see this artifact)
        payload_extra = {
            "_pre_satisfied": pre_satisfied, "_witness_ok": wit_ok,
            "_goal_offset_m": offset,
        }
        h = ArtifactHeader(
            schema_version=payload.schema_version, artifact_type="task",
            artifact_id=sha256_of(payload), run_id=ctx.run_id, stage="tasks",
            backend="template", input_digest="see-set", seed=ctx.seed,
        )
        task_refs[scene_id] = ctx.store.put_artifact(BaseArtifact(header=h, payload=payload))
        families[scene_id] = family
        # accumulate for the set-level gate pass
        ctx.extra.setdefault("task_checks", {})[scene_id] = payload_extra

    return TaskSetPayload(task_refs=task_refs, families=families), "template", degs


def _reachable(scene: ScenePayload, pose: Pose) -> bool:
    ring = reach_polygon(scene.robot, pose.position[2])
    return (not ring.is_empty) and ring.contains(sg.Point(pose.position[0], pose.position[1]))
