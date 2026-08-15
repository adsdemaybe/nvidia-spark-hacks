"""Live retarget stream — Shadow Robot Spatial Demonstration Pipeline spec
section 47.

    POST /spatial/live            create a session -> {session_id}
    WS   /spatial/live/{session_id}

The client sends `HandFrame` JSON; this runs it through `ArmRetargeter`
(Pinocchio IK, spec section 26) and returns `RobotShadowState` -- one hand
frame in, one shadow-robot state out. Pinocchio is Linux-only (see
ar-datapipe's README), so the import is deferred to connection time, same
pattern as twin.py's `ar_sim` import: the route accepts the connection and
then closes it with a clear reason on a platform without it, instead of the
whole app failing to start.

Scoped minimally for Milestone 1: no persistence (that's HumanEpisode
recording's job, spec section 21/Phase 8, a separate concern), no
reconnect/resume. Each session gets its own `ArmRetargeter` (stateful --
warm-starts IK frame to frame) but shares one `IkSolver` across sessions,
since solving is a pure function of its arguments and re-parsing the URDF
per session would be wasteful.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from ar_contracts import HandFrame, RobotEndEffector, RobotShadowState
from fastapi import APIRouter, WebSocket, WebSocketDisconnect


@dataclass
class LiveSessionState:
    session_id: str
    robot_id: str = "so101"


class LiveSessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, LiveSessionState] = {}

    def create(self, robot_id: str = "so101") -> LiveSessionState:
        state = LiveSessionState(session_id=str(uuid.uuid4()), robot_id=robot_id)
        with self._lock:
            self._sessions[state.session_id] = state
        return state

    def get(self, session_id: str) -> LiveSessionState | None:
        with self._lock:
            return self._sessions.get(session_id)


_solver_lock = threading.Lock()
# ar_datapipe.IkSolver, typed loosely so this module doesn't need a
# top-level ar_datapipe import (which would need Linux at import time).
_shared_solver: object | None = None


def _get_shared_solver(robot_id: str):
    global _shared_solver
    from ar_datapipe import IkSolver, RobotModel
    from spatial_providers import FixtureRobotProvider

    with _solver_lock:
        if _shared_solver is None:
            bundle = FixtureRobotProvider().get_robot_bundle(robot_id)
            model = RobotModel(
                urdf_path=bundle.urdf_path,
                end_effector_frame=bundle.manifest.end_effectors[0],
            )
            _shared_solver = IkSolver(model)
        return _shared_solver


def build_router(store: LiveSessionStore) -> APIRouter:
    router = APIRouter(tags=["spatial-live"])

    @router.post("/spatial/live")
    def create_live_session(robot_id: str = "so101") -> dict:
        state = store.create(robot_id)
        return {"session_id": state.session_id}

    @router.websocket("/spatial/live/{session_id}")
    async def live_stream(websocket: WebSocket, session_id: str) -> None:
        state = store.get(session_id)
        if state is None:
            await websocket.close(code=4004, reason=f"unknown session_id {session_id!r}")
            return

        try:
            from ar_datapipe import ArmRetargeter
        except ImportError as exc:
            await websocket.accept()
            await websocket.close(code=1011, reason=f"retargeting unavailable: {exc}")
            return

        await websocket.accept()
        retargeter = ArmRetargeter(_get_shared_solver(state.robot_id))
        try:
            while True:
                raw = await websocket.receive_text()
                hand = HandFrame.model_validate_json(raw)
                result = retargeter.step(hand, timestamp_ns=hand.timestamp_ns)
                shadow = RobotShadowState(
                    timestamp_ns=hand.timestamp_ns,
                    robot_id=state.robot_id,
                    joint_positions=result.q,
                    ik_status=result.ik_status,
                    end_effector=RobotEndEffector(
                        position_m=result.ee_target_position_m,
                        orientation_xyzw=result.ee_target_orientation_xyzw,
                    ),
                )
                await websocket.send_text(shadow.model_dump_json())
        except WebSocketDisconnect:
            pass

    return router
