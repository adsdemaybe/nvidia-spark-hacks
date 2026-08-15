"""Spatial Episodes API — Shadow Robot Spatial Demonstration Pipeline spec
section 46. Sibling router to episodes.py, same create -> artifact -> finish
pattern -- deliberately not editing episodes.py itself, since its artifact
shape (SpatialFrame) is different from this route's (HandFrame + object
states + events).

    POST /spatial/episodes                       create
    POST /spatial/episodes/{episode_id}/artifact  upload recorded hand data
    POST /spatial/episodes/{episode_id}/finish    normalize -> interaction_ir
                                                   -> retarget -> verify -> export
    GET  /spatial/episodes/{episode_id}           status

`/finish` needs ar_datapipe + spatial_providers (Pinocchio/MuJoCo, Linux
only) -- deferred into the route body, same pattern as twin.py/spatial_live.py,
so the app still starts on Windows even though this specific call fails
there with a clear reason.
"""

from __future__ import annotations

from pathlib import Path

from ar_contracts import HandFrame, HumanEpisode, HumanEpisodeEvent, ObjectState, Pose
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .spatial_store import HumanEpisodeStore


class CreateHumanEpisodeRequest(BaseModel):
    task_id: str
    asset_id: str
    hand_provider: str = "mock"


class CreateHumanEpisodeResponse(BaseModel):
    episode_id: str
    status: str


class ArtifactUploadRequest(BaseModel):
    hand_frames: list[HandFrame]
    object_states: list[ObjectState] = []
    events: list[HumanEpisodeEvent] = []


class FinishRequest(BaseModel):
    robot_id: str = "so101"
    asset_world_pose: Pose
    goal_position_m: tuple[float, float, float]
    goal_tolerance_m: float = 0.05
    # Grasp-and-place / pull task predicates (Track C) -- see
    # ar_contracts.simulation_provider.TaskSpec's own docstring for exactly
    # what these check and why. None (the default) keeps the original
    # single-goal "reach" predicate exercised unchanged.
    object_position_m: tuple[float, float, float] | None = None
    object_capture_radius_m: float = 0.05
    pull_axis: tuple[float, float, float] | None = None
    pull_distance_m: float | None = None


class HumanEpisodeStatusResponse(BaseModel):
    episode_id: str
    status: str
    task_success: bool | None = None
    dataset_id: str | None = None
    rejection_reason: str | None = None


def build_router(store: HumanEpisodeStore, dataset_root: Path) -> APIRouter:
    router = APIRouter(prefix="/spatial/episodes", tags=["spatial-episodes"])

    @router.post("", response_model=CreateHumanEpisodeResponse)
    def create_episode(req: CreateHumanEpisodeRequest) -> CreateHumanEpisodeResponse:
        record = store.create(req.task_id, req.asset_id, req.hand_provider)
        return CreateHumanEpisodeResponse(episode_id=record.episode_id, status=record.status)

    @router.post("/{episode_id}/artifact")
    def upload_artifact(episode_id: str, req: ArtifactUploadRequest) -> dict:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        if not req.hand_frames:
            raise HTTPException(422, "artifact upload must include at least one hand frame")
        timestamps = [f.timestamp_ns for f in req.hand_frames]
        if timestamps != sorted(timestamps):
            raise HTTPException(422, "hand_frames must be ordered by non-decreasing timestamp")
        record.hand_frames = req.hand_frames
        record.object_states = req.object_states
        record.events = req.events
        record.status = "uploaded"
        store.update(record)
        return {
            "episode_id": episode_id,
            "status": record.status,
            "n_frames": len(req.hand_frames),
        }

    @router.post("/{episode_id}/finish", response_model=HumanEpisodeStatusResponse)
    def finish_episode(episode_id: str, req: FinishRequest) -> HumanEpisodeStatusResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        if record.status != "uploaded":
            raise HTTPException(
                409,
                f"episode {episode_id!r} has status {record.status!r}, expected 'uploaded'",
            )

        try:
            from ar_datapipe import run_spatial_episode
            from spatial_providers import (
                FixtureAssetProvider,
                FixtureRobotProvider,
                TaskSpec,
                get_configured_simulation_provider,
            )
        except ImportError as exc:
            raise HTTPException(
                503, f"spatial retargeting/verification unavailable on this platform: {exc}",
            ) from exc

        from ar_contracts import HumanEpisodeMetadata

        human_episode = HumanEpisode(
            metadata=HumanEpisodeMetadata(
                episode_id=record.episode_id,
                task_id=record.task_id,
                asset_id=record.asset_id,
                hand_provider=record.hand_provider,
            ),
            hand_frames=record.hand_frames,
            object_states=record.object_states,
            events=record.events,
        )
        robot_bundle = FixtureRobotProvider().get_robot_bundle(req.robot_id)
        asset_bundle = FixtureAssetProvider().get_asset_bundle(record.asset_id)
        task = TaskSpec(
            goal_position_m=req.goal_position_m,
            tolerance_m=req.goal_tolerance_m,
            object_position_m=req.object_position_m,
            object_capture_radius_m=req.object_capture_radius_m,
            pull_axis=req.pull_axis,
            pull_distance_m=req.pull_distance_m,
        )

        result = run_spatial_episode(
            human_episode,
            robot_bundle,
            asset_bundle,
            req.asset_world_pose,
            task,
            dataset_root=dataset_root,
            simulation_provider=get_configured_simulation_provider(),
        )

        record.result = result
        record.status = "accepted" if result.verification.status == "accepted" else "rejected"
        store.update(record)
        return _status_response(record)

    @router.get("/{episode_id}", response_model=HumanEpisodeStatusResponse)
    def get_episode(episode_id: str) -> HumanEpisodeStatusResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        return _status_response(record)

    return router


def _status_response(record) -> HumanEpisodeStatusResponse:
    result = record.result
    return HumanEpisodeStatusResponse(
        episode_id=record.episode_id,
        status=record.status,
        task_success=result.verification.task_success if result else None,
        dataset_id=result.metadata.dataset_id if result else None,
        rejection_reason=result.verification.rejection_reason if result else None,
    )
