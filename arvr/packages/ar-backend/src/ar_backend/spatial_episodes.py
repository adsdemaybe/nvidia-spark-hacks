"""Spatial Episodes API — Shadow Robot Spatial Demonstration Pipeline spec
section 46. Sibling router to episodes.py, same create -> artifact -> finish
pattern -- deliberately not editing episodes.py itself, since its artifact
shape (SpatialFrame) is different from this route's (HandFrame + object
states + events).

    POST /spatial/episodes                       create
    POST /spatial/episodes/{episode_id}/artifact  upload recorded hand data
    POST /spatial/episodes/{episode_id}/finish    normalize -> interaction_ir
                                                   -> retarget -> verify -> export
    POST /spatial/episodes/{episode_id}/export-human
                                                  recorded hands -> LeRobot,
                                                  no robot involved
    GET  /spatial/episodes/{episode_id}           status

`/finish` needs ar_datapipe + spatial_providers (Pinocchio/MuJoCo, Linux
only) -- deferred into the route body, same pattern as twin.py/spatial_live.py,
so the app still starts on Windows even though this specific call fails
there with a clear reason.

`/export-human` is the second, independent exit from a recording, added
because the first one currently has no destination: the robot hand this
project targets is still being generated from a text prompt, so there is no
URDF to retarget onto and `/finish` can only answer 503. It publishes the
`HumanEpisode` at the layer it was captured at -- the human's own joint
positions -- and touches neither retargeting nor verification, so it needs
no Pinocchio, no MuJoCo and no robot bundle, and runs on Windows. It is
additive in both directions: `/finish` is unchanged, and running one does
not consume or invalidate the other, because the two layers are meant to
coexist (a demonstration exported at the human layer today should still
retarget onto the robot hand once it exists).
"""

from __future__ import annotations

from pathlib import Path

from ar_contracts import (
    HandFrame,
    HumanEpisode,
    HumanEpisodeEvent,
    HumanEpisodeMetadata,
    ObjectState,
    Pose,
)
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


class ExportHumanRequest(BaseModel):
    # LeRobot conditions a policy on a natural-language task string, which is
    # not necessarily our internal task_id ("press_button" vs "press the red
    # button"). Optional so the common case stays a bodyless POST.
    task: str | None = None


class HumanExportResponse(BaseModel):
    episode_id: str
    dataset_id: str
    dataset_path: str
    episode_path: str
    n_rows: int
    action_dim: int


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

        human_episode = _human_episode_from_record(record)
        robot_bundle = FixtureRobotProvider().get_robot_bundle(req.robot_id)
        asset_bundle = FixtureAssetProvider().get_asset_bundle(record.asset_id)
        task = TaskSpec(goal_position_m=req.goal_position_m, tolerance_m=req.goal_tolerance_m)

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

    @router.post("/{episode_id}/export-human", response_model=HumanExportResponse)
    def export_human(
        episode_id: str, req: ExportHumanRequest | None = None
    ) -> HumanExportResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        if not record.hand_frames:
            raise HTTPException(
                409,
                f"episode {episode_id!r} has status {record.status!r} and no "
                "uploaded hand frames; upload an artifact before exporting",
            )
        # Deliberately not gated on `status == 'uploaded'` the way /finish is.
        # /finish moves the episode to accepted/rejected, and that verdict is
        # about a *robot* trajectory; it says nothing about whether the human
        # recording is worth exporting, and the raw frames are never mutated
        # by it (see test_raw_hand_frames_intact_after_finish). So exporting
        # the human layer stays available before or after a finish attempt.

        if record.human_export is not None:
            return HumanExportResponse(**record.human_export)

        # `ar_datapipe` imports fine on every platform -- only calls that
        # touch Pinocchio/MuJoCo/pyarrow fail, and this one needs none of the
        # first two. Imported in the body anyway to match this module's
        # existing convention, so there is one rule here and not two.
        from ar_datapipe import export_human_episode

        # Human datasets live under their own `human/` subtree rather than
        # beside the robot exports in `dataset_root/<task_id>/`. Both write
        # `meta/info.json`, and the two schemas are different by design, so
        # sharing a directory would mean whichever exporter ran last decided
        # what the whole dataset claims to contain.
        dataset_dir = dataset_root / "human" / record.task_id
        try:
            result = export_human_episode(
                dataset_dir,
                _human_episode_from_record(record),
                episode_index=_next_episode_index(dataset_dir),
                task=req.task if req else None,
            )
        except ImportError as exc:  # pyarrow missing -- the only hard dep left
            raise HTTPException(503, f"dataset export unavailable: {exc}") from exc
        except ValueError as exc:
            # Recordings this exporter refuses (no tracked joint anywhere,
            # duplicate frames for one hand at one instant, mixed coordinate
            # frames) are the client's data problem, not a server fault.
            raise HTTPException(422, str(exc)) from exc

        response = HumanExportResponse(
            episode_id=episode_id,
            dataset_id=result.dataset_id,
            dataset_path=str(result.dataset_dir),
            episode_path=str(result.episode_path),
            n_rows=result.n_rows,
            action_dim=result.action_dim,
        )
        record.human_export = response.model_dump()
        store.update(record)
        return response

    @router.get("/{episode_id}", response_model=HumanEpisodeStatusResponse)
    def get_episode(episode_id: str) -> HumanEpisodeStatusResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        return _status_response(record)

    return router


def _human_episode_from_record(record) -> HumanEpisode:
    """Stored record -> the contract object both exit paths consume.

    Extracted rather than written twice: the two routes must agree on what a
    HumanEpisode built from a record contains, and the way that stops being
    true is someone adding a field to `HumanEpisodeRecord` and updating only
    one of two identical constructions.
    """
    return HumanEpisode(
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


def _next_episode_index(dataset_dir: Path) -> int:
    """Append rather than overwrite: the index after the highest already in
    `dataset_dir`.

    A LeRobot dataset holds many episodes of one task, so a second recording
    of the same task must not land on `episode_000000.parquet` and silently
    replace the first. Derived from what is on disk because that is the only
    state that survives a backend restart -- the episode store is in-memory.
    """
    data_dir = dataset_dir / "data" / "chunk-000"
    if not data_dir.is_dir():
        return 0
    indices = [
        int(p.stem.removeprefix("episode_"))
        for p in data_dir.glob("episode_*.parquet")
        if p.stem.removeprefix("episode_").isdigit()
    ]
    return max(indices) + 1 if indices else 0


def _status_response(record) -> HumanEpisodeStatusResponse:
    result = record.result
    return HumanEpisodeStatusResponse(
        episode_id=record.episode_id,
        status=record.status,
        task_success=result.verification.task_success if result else None,
        dataset_id=result.metadata.dataset_id if result else None,
        rejection_reason=result.verification.rejection_reason if result else None,
    )
