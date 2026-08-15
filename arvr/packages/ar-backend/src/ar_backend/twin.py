"""Twin stream — spec section 38.

    WS /twin/{scene_id}

Serves `ar_sim`'s MuJoCo-backed live physics twin (see
../../ar-sim/README.md) — real gravity/contact/grasp, not a sine wave. Not
the authoritative twin: OpenUSD in Isaac Sim is, once that's wired (spec
sections 52-53). The route loops the pick-and-place routine and resets the
sim each cycle so the demo runs indefinitely instead of finishing once and
going still — same pattern as Andrew's original standalone
`tools/mock_twin_server.py --source mujoco`, folded into this app instead
of a second process.

`ar_sim` is Linux-only (see its README); on a platform without it this
route accepts the connection and then closes it with a clear reason
instead of the app failing to start. The client's "FIXTURE STREAM" button
never touches this route at all — it fetches
`fixtures/ar-xr/fake_twin_state.jsonl` directly (see xr-web/twinProvider.ts)
— so this route existing or not never blocks fixture-only development.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("struct-ar-twin")

DEFAULT_HZ = 30.0


def build_router(hz: float = DEFAULT_HZ) -> APIRouter:
    router = APIRouter(tags=["twin"])

    @router.websocket("/twin/{scene_id}")
    async def twin_stream(websocket: WebSocket, scene_id: str) -> None:
        await websocket.accept()
        try:
            from ar_sim import MujocoTwinSource, PickAndPlaceDirector
        except ImportError as exc:
            await websocket.close(code=1011, reason=f"ar_sim unavailable: {exc}")
            return

        sim = MujocoTwinSource(scene_id=scene_id, control_hz=hz)
        director = PickAndPlaceDirector()
        period_ticks = max(1, round(director.duration_s * hz))
        period_s = 1.0 / hz

        tick = 0
        try:
            while True:
                if tick > 0 and tick % period_ticks == 0:
                    sim.reset()
                director.drive(sim, (tick % period_ticks) / hz)
                state = sim.step()
                await websocket.send_text(state.model_dump_json())
                tick += 1
                await asyncio.sleep(period_s)
        except WebSocketDisconnect:
            log.info("client detached from scene %s after %d frames", scene_id, tick)

    return router
