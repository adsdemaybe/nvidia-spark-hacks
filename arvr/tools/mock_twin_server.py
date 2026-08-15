#!/usr/bin/env python3
"""STRUCT AR/XR mock TwinState server — spec section 58.

Streams synthetic-but-schema-valid TwinState frames over WebSocket so phone
client development is never blocked on Isaac Sim being wired up on the
Spark. Every message is constructed as a real `ar_contracts.TwinState` and
serialized via `model_dump_json()`, so a client built against this server is
built against the exact same schema Isaac will publish later (spec section
25: "The renderer must not care which provider produced the state" — this
script IS MockTwinStateProvider's wire behavior; IsaacTwinStateProvider only
needs to swap the state source, not the shape).

Usage:
    uv run python tools/mock_twin_server.py --hz 30 --port 8765
    # connect to ws://<host>:8765/twin/<scene_id>
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

import websockets
import websockets.exceptions
from ar_contracts import ObjectState, RobotState, TaskState, TwinState

NUM_JOINTS = 6
DEFAULT_SCENE_ID = "demo_room"


def synthetic_state(scene_id: str, t: float) -> TwinState:
    joints = tuple(round(0.3 * math.sin(t * 0.5 + i), 6) for i in range(NUM_JOINTS))
    cube_x = round(0.3 + 0.05 * math.sin(t * 0.3), 6)
    return TwinState(
        timestamp_ns=time.time_ns(),
        scene_id=scene_id,
        robot=RobotState(id="robot_01", joint_positions=joints),
        objects=(
            ObjectState(
                id="cube_01",
                position_m=(cube_x, 0.1, 0.7),
                orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
            ),
        ),
        task=TaskState(id="cube_to_bin", status="running"),
    )


def _connection_path(websocket) -> str:
    # Works across websockets versions: >=13 exposes websocket.request.path,
    # older versions set websocket.path directly.
    if hasattr(websocket, "path"):
        return websocket.path
    return websocket.request.path


async def handler(websocket, hz: float) -> None:
    path = _connection_path(websocket)
    scene_id = path.rsplit("/", 1)[-1] or DEFAULT_SCENE_ID
    period = 1.0 / hz
    start = time.monotonic()
    print(f"client connected -> streaming scene_id={scene_id!r} at {hz} Hz")
    try:
        while True:
            t = time.monotonic() - start
            state = synthetic_state(scene_id, t)
            await websocket.send(state.model_dump_json())
            await asyncio.sleep(period)
    except websockets.exceptions.ConnectionClosed:
        print("client disconnected")


async def main(host: str, port: int, hz: float) -> None:
    async def bound_handler(websocket):
        await handler(websocket, hz)

    async with websockets.serve(bound_handler, host, port):
        print(f"mock twin server listening on ws://{host}:{port}/twin/<scene_id> at {hz} Hz")
        print("SIMULATION DISCONNECTED — USING FIXTURE STREAM (spec section 82)")
        await asyncio.Future()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hz", type=float, default=30.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.hz))
