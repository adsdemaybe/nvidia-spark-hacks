"""IsaacSimulationProvider — Shadow Robot Spatial Demonstration Pipeline spec
section 31/33 (Isaac Sim as the authoritative final integration verifier).
A thin WebSocket *client* implementing `SimulationProvider`, talking to a
persistent Isaac-side batch verify server
(`packages/isaac-bridge/run_verify_server.py`, SSH/Spark-only — Isaac Sim
ships its own Python runtime, incompatible with this uv workspace's venv,
same reason isaac-bridge's TwinState streamer lives outside the workspace
too). No Isaac/Kit runtime dependency here — "provider hides source-specific
implementation" the same way `MuJoCoSimulationProvider` does for MuJoCo.

The verify server must already be running (started manually via SSH, per
`packages/isaac-bridge/README.md`'s docker-run recipe) — this class never
boots Isaac itself; a per-call Isaac boot (~20s+) inside `replay_and_verify()`
would be unworkable for anything resembling a real demo cadence.
"""

from __future__ import annotations

import asyncio
import json
import os

from ar_contracts import (
    InteractableAsset,
    RobotBundle,
    RobotTrajectory,
    VerificationChecks,
    VerificationResult,
)

from .simulation_provider import SimulationProvider, TaskSpec

DEFAULT_ISAAC_VERIFY_WS_URL = "ws://localhost:8767"


class IsaacVerifyServerUnavailable(ConnectionError):
    """Raised when the Isaac verify server can't be reached at all — a
    connection-level failure, distinct from a real rejected VerificationResult
    (which the server itself returns; that's a normal outcome, not this)."""


class IsaacSimulationProvider(SimulationProvider):
    def __init__(self, ws_url: str | None = None) -> None:
        env_url = os.environ.get("ISAAC_VERIFY_WS_URL", DEFAULT_ISAAC_VERIFY_WS_URL)
        self.ws_url = ws_url or env_url

    def replay_and_verify(
        self,
        robot_bundle: RobotBundle,
        asset_bundle: InteractableAsset,
        trajectory: RobotTrajectory,
        task: TaskSpec,
    ) -> VerificationResult:
        del asset_bundle  # not used this round -- same scope as MuJoCoSimulationProvider;
        # the verify server resolves the robot from its own copy of the same
        # fixture tree by robot_id, not from anything sent over the wire.

        request = {
            "robot_id": robot_bundle.manifest.robot_id,
            "trajectory": {
                "trajectory_id": trajectory.metadata.trajectory_id,
                "frames": [
                    {
                        "timestamp_ns": f.timestamp_ns,
                        "q": list(f.q),
                        "dq": list(f.dq),
                        "end_effector_position_m": list(f.end_effector_position_m),
                        "gripper": f.gripper,
                        "ik_status": f.ik_status,
                    }
                    for f in trajectory.frames
                ],
            },
            "task": {
                "goal_position_m": list(task.goal_position_m),
                "tolerance_m": task.tolerance_m,
            },
        }

        try:
            raw = asyncio.run(self._send(json.dumps(request)))
        except IsaacVerifyServerUnavailable:
            raise
        except Exception as exc:
            raise IsaacVerifyServerUnavailable(
                f"could not reach Isaac verify server at {self.ws_url}: {exc}"
            ) from exc

        payload = json.loads(raw)
        checks = VerificationChecks(**payload["checks"])
        return VerificationResult(
            episode_id=trajectory.metadata.trajectory_id,
            status=payload["status"],
            checks=checks,
            tracking_error_m=payload.get("tracking_error_m"),
            task_success=payload.get("task_success"),
            dataset_id=payload.get("dataset_id"),
            rejection_reason=payload.get("rejection_reason"),
        )

    async def _send(self, request_json: str) -> str:
        import websockets

        try:
            async with websockets.connect(self.ws_url, open_timeout=5) as ws:
                await ws.send(request_json)
                return await ws.recv()
        except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
            raise IsaacVerifyServerUnavailable(
                f"could not reach Isaac verify server at {self.ws_url}: {exc}"
            ) from exc
