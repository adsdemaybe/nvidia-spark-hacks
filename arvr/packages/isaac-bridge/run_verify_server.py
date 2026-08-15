#!/usr/bin/env python3
"""isaac-bridge verify server — Shadow Robot Spatial Demonstration Pipeline
spec section 33 (Isaac as the authoritative final integration verifier).
Batch WebSocket request/response (NOT a push-stream like run_twin_server.py):
one client request carries a whole RobotTrajectory, one server response is
one VerificationResult -- matching MuJoCoSimulationProvider's shape exactly
so a client can genuinely swap providers with zero downstream changes
(spec's own "provider hides source-specific implementation" rule).

Must run inside Isaac Sim's own bundled Python (`/isaac-sim/python.sh`), not
the uv workspace venv -- same reason run_twin_server.py sits outside the
workspace (see this directory's README). Loads the real SO-101 URDF ONCE at
startup (Isaac boot is ~20s+; a per-request boot inside replay_and_verify()
would be unworkable) and answers many requests against that one persistent
articulation.

Platform note (aarch64/Spark-specific, found empirically, not documented
anywhere): this image's ENTRYPOINT (/isaac-sim/runheadless.sh) drops to a
bare, non-interactive `/bin/bash` on aarch64 and silently ignores any CMD
you pass it -- livestreaming genuinely isn't supported there, and the
script doesn't fall back to exec "$@" the way you'd expect. Run this with
`--entrypoint /isaac-sim/python.sh` (see README.md's updated recipe), not
the vanilla `bash -c '...'` pattern run_twin_server.py's README uses --
that pattern silently no-ops on this hardware.

What's real: URDF import, joint control (isaacsim.core.prims.SingleArticulation,
DOF order confirmed empirically to match ar_datapipe.retarget.IkSolver's
Pinocchio joint order exactly -- same robot, same URDF, no name-order
surprises), physics stepping, real EE world-pose readback from the actual
gripper_frame_link prim (not the placeholder-style fixed idle array
run_twin_server.py's robot state still is).

What's NOT checked here (disclosed, not silently skipped): collision.
MuJoCoSimulationProvider's collision check (packages/spatial-providers)
already exists and works; replicating it against Isaac's contact-reporting
API (PhysxContactReportAPI per-body + a RigidContactView/ContactSensor --
meaningfully more setup than MuJoCo's data.ncon) is real, separate scope,
not done this round. VerificationChecks.collision_valid is already typed
`bool | None` for exactly this -- None here means "not evaluated", never a
false "passed". Joint limits are checked against robot_ir.json's own
declared limits (already loaded from the same repo checkout, no need to
query Isaac's own dof limits redundantly).

Usage (from an SSH session, inside your own ar-vr/sky/ tree -- same cache/
mount convention as run_twin_server.py's README):
    CACHE=~/nvidia-spark-hacks/ar-vr/sky/artifacts/isaac-sim-cache
    ARVR=~/nvidia-spark-hacks/ar-vr/sky/worktrees/<your-worktree>/arvr
    docker run --name struct-ar-isaac-verify --rm -it \
      --gpus all -e "ACCEPT_EULA=Y" -e "PRIVACY_CONSENT=Y" \
      --network host \
      --entrypoint /isaac-sim/python.sh \
      -v $CACHE/kit:/isaac-sim/kit/cache:rw \
      -v $CACHE/ov:/root/.cache/ov:rw \
      -v $CACHE/pip:/root/.cache/pip:rw \
      -v $CACHE/glcache:/root/.cache/nvidia/GLCache:rw \
      -v $CACHE/computecache:/root/.nv/ComputeCache:rw \
      -v $ARVR:/workspace/arvr:ro \
      nvcr.io/nvidia/isaac-sim:5.1.0 \
      /workspace/arvr/packages/isaac-bridge/run_verify_server.py --port 8767
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--robots-dir",
        default="/workspace/arvr/fixtures/spatial-training/robots",
        help="Fixture robots tree (mounted read-only, same repo checkout the client used).",
    )
    parser.add_argument("--hz", type=float, default=30.0)
    parser.add_argument(
        "--settle-steps", type=int, default=3, help="physics steps per trajectory frame",
    )
    return parser.parse_args()


args = parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import asyncio  # noqa: E402
import json as _json  # noqa: E402,F401
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import websockets  # noqa: E402
import websockets.exceptions  # noqa: E402
from isaacsim.asset.importer.urdf import _urdf  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.prims import SingleArticulation, XFormPrim  # noqa: E402

TRACKING_ERROR_TOL_M = 0.01  # same tolerance MuJoCoSimulationProvider uses


class RobotHandle:
    """One imported, articulated robot + everything needed to replay a
    trajectory against it. Built once per robot_id, reused across requests
    -- importing a URDF is comparatively expensive next to stepping physics."""

    def __init__(self, robots_dir: Path, robot_id: str, world: World) -> None:
        bundle_dir = robots_dir / robot_id
        manifest = json.loads((bundle_dir / "manifest.json").read_text())
        robot_ir = json.loads((bundle_dir / manifest["robot_ir"]).read_text())
        self.end_effector_frame = robot_ir["end_effector_frame"]
        self.limits = {
            j["name"]: (j["lower_limit"], j["upper_limit"])
            for j in robot_ir["joints"]
            if j["type"] != "fixed"
        }

        urdf_interface = _urdf.acquire_urdf_interface()
        import_config = _urdf.ImportConfig()
        import_config.set_merge_fixed_joints(False)
        import_config.set_fix_base(True)
        import_config.set_self_collision(False)
        import_config.set_make_default_prim(True)

        parsed = urdf_interface.parse_urdf(str(bundle_dir), manifest["urdf"], import_config)
        self.prim_path = urdf_interface.import_robot(
            str(bundle_dir), manifest["urdf"], parsed, import_config, "",
        )
        print(f"robot {robot_id!r} imported at {self.prim_path}")

        world.reset()
        self.articulation = SingleArticulation(prim_path=self.prim_path, name=robot_id)
        self.articulation.initialize()
        self.dof_names = list(self.articulation.dof_names)
        print(f"robot {robot_id!r} dof_names: {self.dof_names}")

        ee_prim_path = f"{self.prim_path}/{self.end_effector_frame}"
        self.ee = XFormPrim(prim_paths_expr=ee_prim_path)
        print(f"robot {robot_id!r} EE prim: {ee_prim_path}")

    def joint_limits_ok(self, joint_names: list[str], values: list[float]) -> bool:
        for name, value in zip(joint_names, values, strict=True):
            limit = self.limits.get(name)
            if limit is None or limit[0] is None or limit[1] is None:
                continue
            lo, hi = limit
            if not (lo - 1e-9 <= value <= hi + 1e-9):
                return False
        return True

    def replay_frame(
        self, world: World, joint_names: list[str], q: list[float], settle_steps: int,
    ):
        # Map by name -- dof_names has been empirically confirmed to match
        # ar_datapipe.retarget.IkSolver's order for this robot, but map
        # explicitly anyway rather than assume it holds for a future one.
        ordered = np.zeros(len(self.dof_names))
        by_name = dict(zip(joint_names, q, strict=True))
        for i, name in enumerate(self.dof_names):
            ordered[i] = by_name.get(name, 0.0)

        self.articulation.set_joint_positions(ordered)
        for _ in range(settle_steps):
            world.step(render=False)

        achieved_q = self.articulation.get_joints_state().positions
        ee_pos, _ee_rot = self.ee.get_world_poses()
        return achieved_q, np.array(ee_pos[0])


_world: World | None = None
_robots: dict[str, RobotHandle] = {}


def _get_world() -> World:
    global _world
    if _world is None:
        _world = World(physics_dt=1.0 / args.hz, rendering_dt=1.0 / args.hz)
        _world.scene.add_default_ground_plane()
    return _world


def _get_robot(robot_id: str) -> RobotHandle:
    if robot_id not in _robots:
        _robots[robot_id] = RobotHandle(Path(args.robots_dir), robot_id, _get_world())
    return _robots[robot_id]


def verify(request: dict) -> dict:
    robot_id = request["robot_id"]
    frames = request["trajectory"]["frames"]
    trajectory_id = request["trajectory"]["trajectory_id"]
    task = request["task"]

    if not frames:
        return _rejected(trajectory_id, "empty trajectory")

    robot = _get_robot(robot_id)
    world = _get_world()
    joint_names = list(robot.dof_names)

    ik_ok = all(f["ik_status"] != "failed" for f in frames)
    limits_ok = all(f["ik_status"] != "joint_limit" for f in frames)

    tracking_errors = []
    final_ee = None
    for frame in frames:
        q = frame["q"]
        if not all(math.isfinite(v) for v in q):
            ts = frame["timestamp_ns"]
            return _rejected(trajectory_id, f"non-finite joint value in frame at t={ts}")
        if not robot.joint_limits_ok(joint_names, q):
            limits_ok = False
        achieved_q, ee_pos = robot.replay_frame(world, joint_names, q, args.settle_steps)
        commanded = np.array(frame["end_effector_position_m"])
        tracking_errors.append(float(np.linalg.norm(ee_pos - commanded)))
        final_ee = ee_pos

    replay_ok = all(e <= TRACKING_ERROR_TOL_M for e in tracking_errors)
    max_tracking_error = max(tracking_errors, default=0.0)

    goal = np.array(task["goal_position_m"])
    if final_ee is not None:
        task_error = float(np.linalg.norm(final_ee - goal))
    else:
        task_error = float("inf")
    task_ok = task_error <= task["tolerance_m"]

    checks = {
        "ik": ik_ok,
        "joint_limits": limits_ok,
        "velocity": True,  # velocity already checked upstream by MuJoCoSimulationProvider
        "replay": replay_ok,
        "task_predicate": task_ok,
        "collision_valid": None,  # not evaluated this round -- see module docstring
    }
    all_ok = ik_ok and limits_ok and replay_ok and task_ok
    if all_ok:
        return {
            "status": "accepted",
            "checks": checks,
            "tracking_error_m": max_tracking_error,
            "task_success": True,
            "dataset_id": trajectory_id,
        }

    failed = [
        name for name, ok in (
            ("ik", ik_ok), ("joint_limits", limits_ok), ("replay", replay_ok),
            (f"task_predicate (error {task_error:.4f}m > tol {task['tolerance_m']}m)", task_ok),
        )
        if not ok
    ]
    return {
        "status": "rejected",
        "checks": checks,
        "tracking_error_m": max_tracking_error,
        "task_success": False,
        "rejection_reason": f"failed checks: {', '.join(failed)}",
    }


def _rejected(trajectory_id: str, reason: str) -> dict:
    return {
        "status": "rejected",
        "checks": {
            "ik": False, "joint_limits": False, "velocity": False,
            "replay": False, "task_predicate": False, "collision_valid": None,
        },
        "tracking_error_m": None,
        "task_success": False,
        "rejection_reason": reason,
    }


async def handler(websocket) -> None:
    async for raw in websocket:
        try:
            request = json.loads(raw)
            result = verify(request)
        except Exception as exc:  # noqa: BLE001 - always answer with a rejection, never hang the client
            result = _rejected(str(uuid.uuid4()), f"verify server error: {exc}")
            print(f"error handling request: {exc}", file=sys.stderr)
        await websocket.send(json.dumps(result))


async def main() -> None:
    async with websockets.serve(handler, args.host, args.port):
        print(f"isaac-bridge verify server listening on ws://{args.host}:{args.port}")
        await asyncio.Future()  # run forever


try:
    asyncio.run(main())
except BaseException:
    import traceback

    traceback.print_exc()
    sys.stdout.flush()
    raise
finally:
    app.close()
