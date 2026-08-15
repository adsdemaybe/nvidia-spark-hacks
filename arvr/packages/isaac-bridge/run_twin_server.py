#!/usr/bin/env python3
"""isaac-bridge — publishes real Isaac Sim physics state as ar_contracts.TwinState
over a WebSocket, mirroring ../../tools/mock_twin_server.py's wire shape exactly
(spec section 25: "the renderer must not care which provider produced the
state" — that script IS MockTwinStateProvider's wire behavior; this one only
swaps the state source, not the shape). xr-web's WebSocketTwinStateProvider
needs no changes to talk to either.

Must run inside Isaac Sim's own bundled Python (`/isaac-sim/python.sh`), not
the uv workspace venv — Isaac Sim ships its own Kit runtime, incompatible
with a normal venv. That's why this package has no pyproject.toml and sits
outside the uv workspace: it is Spark/Isaac-only, SSH-driven, never run on a
dev laptop (see ../../STATE.md).

Robot `joint_positions` are currently a fixed idle placeholder — no
articulated robot is wired into the scene yet (the natural next increment;
see README.md). The cube is what's real here: genuine PhysX gravity and
contact, read back from the stage every physics step, not synthetic math —
the same kind of proof ar_sim's MuJoCo twin already gives, just sourced from
Isaac instead.

Isaac's native quaternion convention is (w, x, y, z); STRUCT's canonical
convention is (x, y, z, w) (see CLAUDE.md). That reordering happens once,
here, at the boundary — nothing downstream re-interprets it.

Usage (inside the isaac-sim container, with ar_contracts + websockets
installed into Isaac's python — see README.md):
    /isaac-sim/python.sh run_twin_server.py --hz 30 --port 8766 --max-frames 0
    # connect to ws://<host>:8766/twin/<scene_id>
"""

from __future__ import annotations

import argparse
import sys

# Isaac Sim's teardown has been observed to exit before Python's normal
# stdout buffering flushes (plain print() calls vanished entirely on a run
# that otherwise completed cleanly) -- force unbuffered output so every
# print actually reaches the log.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hz", type=float, default=30.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="stop after this many physics steps; 0 runs forever (default)",
    )
    return parser.parse_args()


args = parse_args()

# SimulationApp must exist before any isaacsim.* submodule is imported — the
# Kit runtime it boots is what makes those imports resolve at all.
from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True})

import asyncio  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import websockets  # noqa: E402
import websockets.exceptions  # noqa: E402
from ar_contracts import ObjectState, RobotState, TaskState, TwinState  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.api.objects import DynamicCuboid  # noqa: E402

DEFAULT_SCENE_ID = "demo_room"
NUM_JOINTS = 6
IDLE_JOINTS = tuple(0.0 for _ in range(NUM_JOINTS))

world = World(physics_dt=1.0 / args.hz, rendering_dt=1.0 / args.hz)
world.scene.add_default_ground_plane()
# isaacsim.core.api's object wrappers expect numpy arrays for
# position/scale/color, not plain lists (PreviewSurface.__init__ calls
# color.tolist() directly) -- found by running this, not by reading the API.
cube = world.scene.add(
    DynamicCuboid(
        prim_path="/World/cube_01",
        name="cube_01",
        position=np.array([0.3, 0.1, 0.7]),
        scale=np.array([0.08, 0.08, 0.08]),
        color=np.array([0.2, 0.6, 1.0]),
    ),
)
world.reset()


def real_state(scene_id: str) -> TwinState:
    position, orientation_wxyz = cube.get_world_pose()
    w, x, y, z = (float(c) for c in orientation_wxyz)
    return TwinState(
        timestamp_ns=time.time_ns(),
        scene_id=scene_id,
        robot=RobotState(id="robot_01", joint_positions=IDLE_JOINTS),
        objects=(
            ObjectState(
                id="cube_01",
                position_m=tuple(round(float(p), 6) for p in position),
                orientation_xyzw=(round(x, 6), round(y, 6), round(z, 6), round(w, 6)),
            ),
        ),
        task=TaskState(id="cube_to_bin", status="running"),
    )


def _connection_path(websocket) -> str:
    # Works across websockets versions: >=13 exposes websocket.request.path,
    # older versions set websocket.path directly. Same helper as
    # mock_twin_server.py, kept identical on purpose.
    if hasattr(websocket, "path"):
        return websocket.path
    return websocket.request.path


clients: set = set()


async def handler(websocket) -> None:
    scene_id = _connection_path(websocket).rsplit("/", 1)[-1] or DEFAULT_SCENE_ID
    clients.add(websocket)
    print(f"client connected -> scene_id={scene_id!r}")
    try:
        async for _ in websocket:
            pass  # this server only publishes; it doesn't expect client messages
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.discard(websocket)
        print("client disconnected")


async def sim_loop(hz: float, max_frames: int) -> None:
    period = 1.0 / hz
    frame = 0
    while max_frames == 0 or frame < max_frames:
        world.step(render=False)
        if clients:
            payload = real_state(DEFAULT_SCENE_ID).model_dump_json()
            await asyncio.gather(
                *(ws.send(payload) for ws in list(clients)), return_exceptions=True,
            )
        frame += 1
        await asyncio.sleep(period)
    print(f"reached --max-frames={max_frames}, stopping")


async def main() -> None:
    async with websockets.serve(handler, args.host, args.port):
        print(
            f"isaac-bridge listening on ws://{args.host}:{args.port}"
            f"/twin/<scene_id> at {args.hz} Hz (real PhysX cube, placeholder robot)",
        )
        await sim_loop(args.hz, args.max_frames)


try:
    asyncio.run(main())
except BaseException:
    import traceback

    traceback.print_exc()
    sys.stdout.flush()
    raise
finally:
    app.close()
