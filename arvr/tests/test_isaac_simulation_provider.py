"""Acceptance-gate tests for IsaacSimulationProvider — a pure WebSocket
client, tested against a fake local verify server (websockets.sync.server,
no Spark/Isaac needed). The real Isaac-side server
(packages/isaac-bridge/run_verify_server.py) is verified live over SSH
separately (see STATE.md); these tests only exercise the client's own
request/response handling.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import pytest
from ar_contracts import (
    RobotBundle,
    RobotCapabilityProfile,
    RobotIR,
    RobotManifest,
    RobotTrajectory,
    RobotTrajectoryFrame,
    RobotTrajectoryMetadata,
)
from spatial_providers import IsaacSimulationProvider, IsaacVerifyServerUnavailable, TaskSpec

websockets_sync_server = pytest.importorskip("websockets.sync.server")


def _robot_bundle() -> RobotBundle:
    manifest = RobotManifest(
        robot_id="so101",
        source="fixture",
        robot_ir="robot_ir.json",
        urdf="robot.urdf",
        visual_glb="visual_meshes.json",
        base_link="base_link",
        end_effectors=["gripper_frame_link"],
    )
    robot_ir = RobotIR(
        robot_id="so101",
        base_link="base_link",
        end_effector_frame="gripper_frame_link",
        links=("base_link", "gripper_frame_link"),
        joints=(),
    )
    capability_profile = RobotCapabilityProfile(
        arm_dof=5, end_effector="parallel_gripper", workspace_radius_m=0.4,
    )
    return RobotBundle(
        manifest=manifest,
        robot_ir=robot_ir,
        capability_profile=capability_profile,
        urdf_path=Path("robot.urdf"),
        visual_glb_path=Path("visual_meshes.json"),
    )


def _trajectory() -> RobotTrajectory:
    metadata = RobotTrajectoryMetadata(
        trajectory_id=str(uuid.uuid4()),
        robot_id="so101",
        source_human_episode_id=str(uuid.uuid4()),
        robot_bundle_hash="a" * 64,
        asset_bundle_hash="b" * 64,
    )
    frame = RobotTrajectoryFrame(
        timestamp_ns=1, q=(0.0,) * 6, dq=(0.0,) * 6,
        end_effector_position_m=(0.37, 0.07, 0.2), end_effector_orientation_xyzw=(0, 0, 0, 1),
        gripper=0.0, ik_status="ok",
    )
    return RobotTrajectory(metadata=metadata, frames=[frame])


def _task() -> TaskSpec:
    return TaskSpec(goal_position_m=(0.37, 0.07, 0.2), tolerance_m=0.05)


def _run_fake_server(response: dict, port_holder: list, ready: threading.Event):
    def handler(connection):
        _raw = connection.recv()
        connection.send(json.dumps(response))

    with websockets_sync_server.serve(handler, "localhost", 0) as server:
        port_holder.append(server.socket.getsockname()[1])
        ready.set()
        server.serve_forever()


def _start_fake_server(response: dict):
    port_holder: list[int] = []
    ready = threading.Event()
    args = (response, port_holder, ready)
    thread = threading.Thread(target=_run_fake_server, args=args, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    return port_holder[0]


def test_accepted_response_parses_into_a_valid_verification_result():
    trajectory = _trajectory()
    response = {
        "status": "accepted",
        "checks": {
            "ik": True, "joint_limits": True, "velocity": True,
            "replay": True, "task_predicate": True, "collision_valid": None,
        },
        "tracking_error_m": 0.001,
        "task_success": True,
        "dataset_id": trajectory.metadata.trajectory_id,
    }
    port = _start_fake_server(response)

    provider = IsaacSimulationProvider(ws_url=f"ws://localhost:{port}")
    result = provider.replay_and_verify(_robot_bundle(), None, trajectory, _task())

    assert result.status == "accepted"
    assert result.checks.collision_valid is None
    assert result.dataset_id == trajectory.metadata.trajectory_id


def test_rejected_response_carries_a_measurable_reason():
    response = {
        "status": "rejected",
        "checks": {
            "ik": True, "joint_limits": True, "velocity": True,
            "replay": False, "task_predicate": False, "collision_valid": None,
        },
        "tracking_error_m": 0.5,
        "task_success": False,
        "rejection_reason": "failed checks: replay, task_predicate",
    }
    port = _start_fake_server(response)

    provider = IsaacSimulationProvider(ws_url=f"ws://localhost:{port}")
    result = provider.replay_and_verify(_robot_bundle(), None, _trajectory(), _task())

    assert result.status == "rejected"
    assert result.rejection_reason is not None


def test_unreachable_server_raises_a_clear_error_not_a_hang():
    provider = IsaacSimulationProvider(ws_url="ws://localhost:1")  # nothing listens on port 1
    with pytest.raises(IsaacVerifyServerUnavailable):
        provider.replay_and_verify(_robot_bundle(), None, _trajectory(), _task())


def test_get_configured_simulation_provider_selects_isaac(monkeypatch):
    from spatial_providers import get_configured_simulation_provider

    monkeypatch.setenv("STRUCT_SIMULATION_PROVIDER", "isaac")
    assert isinstance(get_configured_simulation_provider(), IsaacSimulationProvider)


def test_get_configured_simulation_provider_rejects_unknown_kind(monkeypatch):
    from spatial_providers import get_configured_simulation_provider

    monkeypatch.setenv("STRUCT_SIMULATION_PROVIDER", "not_a_real_simulator")
    with pytest.raises(ValueError, match="not_a_real_simulator"):
        get_configured_simulation_provider()
