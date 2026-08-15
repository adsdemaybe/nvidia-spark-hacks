"""Follow stream — spec section 39.

    POST /xr/follow            create a session -> {session_id}
    WS   /xr/follow/{session_id}

The client sends `FollowState` (its own human_pose + already-computed
follow_target — the target math is the client's job, spec section 22);
this returns `TwinState`.

AR/XR supplies the follow target only. It does not implement robot
navigation (spec sections 21-24: "The client does not decide how robot
avoids table / turns wheels / navigates doorway"). What moves the
`robot_base` object here is a deliberately simple, capped-speed
straight-line chase toward the target — a placeholder stand-in in the same
spirit as `ar_sim`'s scripted `PickAndPlaceDirector` ("nothing here plans
and nothing here is a learned policy"), not a claim about real navigation.
A real deployment hands `follow_target` to an actual navigation stack
instead of this function.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from ar_contracts import FollowState, ObjectState, RobotState, TaskState, TwinState
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Placeholder chase speed cap — not a real robot's spec, just fast enough
# that FOLLOW mode visibly responds to a moving target in a demo.
MAX_CHASE_SPEED_M_S = 0.8


@dataclass
class FollowSessionState:
    session_id: str
    scene_id: str = "demo_room"
    robot_position_m: tuple[float, float, float] = (0.15, -0.7, 0.0)
    last_update_ns: int | None = None


class FollowSessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, FollowSessionState] = {}

    def create(self, scene_id: str = "demo_room") -> FollowSessionState:
        state = FollowSessionState(session_id=str(uuid.uuid4()), scene_id=scene_id)
        with self._lock:
            self._sessions[state.session_id] = state
        return state

    def get(self, session_id: str) -> FollowSessionState | None:
        with self._lock:
            return self._sessions.get(session_id)


def _chase_step(state: FollowSessionState, follow: FollowState) -> None:
    """Move `robot_position_m` toward the target at a capped speed. Pure
    function of state + one message — no queue, no timers, so a client
    that stops sending simply stops motion (spec section 64: "STOP
    immediately halts target generation" starts on the client, but this
    must not keep moving on its own either)."""
    target = follow.follow_target.position_m
    now = follow.timestamp_ns
    dt = 0.0 if state.last_update_ns is None else max(0.0, (now - state.last_update_ns) / 1e9)
    state.last_update_ns = now
    if dt <= 0:
        return

    cur = state.robot_position_m
    delta = tuple(t - c for t, c in zip(target, cur, strict=True))
    dist = sum(d * d for d in delta) ** 0.5
    if dist < 1e-6:
        return
    step = min(MAX_CHASE_SPEED_M_S * dt, dist)
    frac = step / dist
    state.robot_position_m = tuple(c + d * frac for c, d in zip(cur, delta, strict=True))


def _twin_state(state: FollowSessionState, follow: FollowState) -> TwinState:
    return TwinState(
        timestamp_ns=follow.timestamp_ns,
        scene_id=state.scene_id,
        # Arm joints aren't the point of Follow mode — the base position is.
        # A home pose, not a claim the arm is doing anything.
        robot=RobotState(id="robot_01", joint_positions=(0.0,) * 6),
        objects=(
            ObjectState(id="robot_base", position_m=state.robot_position_m),
            ObjectState(id="follow_target", position_m=follow.follow_target.position_m),
            ObjectState(id="human", position_m=follow.human_pose.position_m),
        ),
        task=TaskState(id="follow_human", status="running"),
    )


def build_router(store: FollowSessionStore) -> APIRouter:
    router = APIRouter(tags=["follow"])

    @router.post("/xr/follow")
    def create_follow_session() -> dict:
        state = store.create()
        return {"session_id": state.session_id}

    @router.websocket("/xr/follow/{session_id}")
    async def follow_stream(websocket: WebSocket, session_id: str) -> None:
        state = store.get(session_id)
        if state is None:
            await websocket.close(code=4004, reason=f"unknown session_id {session_id!r}")
            return
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                follow = FollowState.model_validate_json(raw)
                _chase_step(state, follow)
                await websocket.send_text(_twin_state(state, follow).model_dump_json())
        except WebSocketDisconnect:
            pass

    return router
