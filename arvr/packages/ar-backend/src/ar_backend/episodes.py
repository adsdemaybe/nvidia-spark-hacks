"""Episodes API — spec section 36.

    POST /xr/episodes                       create
    POST /xr/episodes/{episode_id}/artifact  upload recorded frames
    POST /xr/episodes/{episode_id}/finish    normalize -> retarget -> verify -> export
    GET  /xr/episodes/{episode_id}           status

Deviation from the literal spec text, flagged: section 36 says "prefer
completed Parquet artifact upload over thousands of HTTP requests" — the
artifact endpoint here takes a single JSON body of SpatialFrames instead.
That still respects the "one upload, not one-frame-per-request" intent;
swap for real Parquet upload if a client actually needs to stream >>1000
frames (see ar-datapipe/README.md's LeRobot-export caveat for the same
JSONL-vs-Parquet tradeoff made elsewhere in this repo).

`/finish` runs the pipeline synchronously — for a demo-scale episode
(recorded in `ar_datapipe`, confirmed <1s for 105 frames) that's simpler
than standing up a task queue, and rule 85.9 ("never mark verification
success from an LLM judgment") is easier to keep true when there's no
async boundary for a judgment to sneak across.
"""

from __future__ import annotations

from pathlib import Path

from ar_contracts import EpisodeSource, SpatialEpisode, SpatialEvent, SpatialFrame
from ar_datapipe import run_episode as run_pipeline
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .store import EpisodeStore


class CreateEpisodeRequest(BaseModel):
    task_id: str
    source: EpisodeSource
    coordinate_frame: str = "struct_world"


class CreateEpisodeResponse(BaseModel):
    episode_id: str
    status: str


class ArtifactUploadRequest(BaseModel):
    frames: list[SpatialFrame]
    events: list[SpatialEvent] = []


class FinishRequest(BaseModel):
    goal_position_m: tuple[float, float, float]
    goal_tolerance_m: float | None = None


class EpisodeStatusResponse(BaseModel):
    episode_id: str
    status: str
    tracking_error_m: float | None = None
    task_success: bool | None = None
    dataset_id: str | None = None
    rejection_reason: str | None = None


def build_router(store: EpisodeStore, dataset_root: Path) -> APIRouter:
    router = APIRouter(prefix="/xr/episodes", tags=["episodes"])

    @router.post("", response_model=CreateEpisodeResponse)
    def create_episode(req: CreateEpisodeRequest) -> CreateEpisodeResponse:
        record = store.create(req.task_id, req.source, req.coordinate_frame)
        return CreateEpisodeResponse(episode_id=record.episode_id, status=record.status)

    @router.post("/{episode_id}/artifact")
    def upload_artifact(episode_id: str, req: ArtifactUploadRequest) -> dict:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        if not req.frames:
            raise HTTPException(422, "artifact upload must include at least one frame")
        record.frames = req.frames
        record.events = req.events
        record.status = "uploaded"
        store.update(record)
        return {"episode_id": episode_id, "status": record.status, "n_frames": len(req.frames)}

    @router.post("/{episode_id}/finish", response_model=EpisodeStatusResponse)
    def finish_episode(episode_id: str, req: FinishRequest) -> EpisodeStatusResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        if record.status != "uploaded":
            raise HTTPException(
                409, f"episode {episode_id!r} has status {record.status!r}, expected 'uploaded'"
            )

        episode = SpatialEpisode(
            episode_id=record.episode_id,
            task_id=record.task_id,
            source=record.source,
            coordinate_frame=record.coordinate_frame,
            frames_artifact="<in-memory>",
            events=tuple(record.events),
        )
        kwargs = {"goal_position_m": req.goal_position_m, "dataset_root": dataset_root}
        if req.goal_tolerance_m is not None:
            kwargs["goal_tolerance_m"] = req.goal_tolerance_m
        result = run_pipeline(episode, record.frames, **kwargs)

        record.result = result
        record.status = result.status
        store.update(record)
        return _status_response(record)

    @router.get("/{episode_id}", response_model=EpisodeStatusResponse)
    def get_episode(episode_id: str) -> EpisodeStatusResponse:
        record = store.get(episode_id)
        if record is None:
            raise HTTPException(404, f"unknown episode_id {episode_id!r}")
        return _status_response(record)

    return router


def _status_response(record) -> EpisodeStatusResponse:
    result = record.result
    return EpisodeStatusResponse(
        episode_id=record.episode_id,
        status=record.status,
        tracking_error_m=result.tracking_error_m if result else None,
        task_success=result.task_success if result else None,
        dataset_id=result.dataset_id if result else None,
        rejection_reason=result.rejection_reason if result else None,
    )
