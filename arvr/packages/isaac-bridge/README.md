# packages/isaac-bridge — Sky, SSH-only, Spark-only

Publishes **real Isaac Sim physics** as `ar_contracts.TwinState` over a
WebSocket, at the exact same wire shape `tools/mock_twin_server.py` already
streams — spec section 25's "the renderer must not care which provider
produced the state." `xr-web`'s `WebSocketTwinStateProvider` needs zero
changes to point at this instead of the mock or MuJoCo (`ar_backend`'s
`/twin/{scene_id}` route).

## Why this isn't a normal uv workspace member

Isaac Sim ships its own bundled Python + Kit runtime (`/isaac-sim/python.sh`
inside the container) — it is not, and cannot be, the `arvr` uv workspace's
venv. `ar_contracts` and `websockets` get installed *into that runtime*
instead (see below). There is deliberately no `pyproject.toml` here — this
package is a script meant to run inside the Isaac Sim container on the
Spark, never on a dev laptop, never imported by the rest of `arvr`.

## What's real and what's a placeholder right now

- **Real**: a `DynamicCuboid` under actual PhysX gravity/contact, in a real
  Isaac `World`. Its pose is read back from the stage every physics step —
  the same kind of proof `ar_sim`'s MuJoCo twin already gives for TEACH/
  CORRECT, just sourced from Isaac's simulator instead of MuJoCo's.
- **Placeholder**: `TwinState.robot.joint_positions` is a fixed idle array —
  no articulated robot is in the scene yet. Wiring in a real one (via
  Isaac's URDF importer, likely targeting the same placeholder arm
  `ar_datapipe` and `ar_sim` already use) is the natural next increment,
  not done here — matches this codebase's rule against inventing state that
  isn't actually simulated.

## Running it (from an SSH session, inside your own `ar-vr/sky/` tree)

```bash
CACHE=~/nvidia-spark-hacks/ar-vr/sky/artifacts/isaac-sim-cache
ARVR=~/nvidia-spark-hacks/ar-vr/sky/worktrees/<your-worktree>/arvr

# One-time per container: install the two pure-Python deps into Isaac's
# bundled interpreter. websockets needs a network-reachable pip index;
# ar_contracts installs from the mounted repo, no network needed.
docker run --name struct-ar-isaac-bridge --rm -it \
  --gpus all -e "ACCEPT_EULA=Y" -e "PRIVACY_CONSENT=Y" \
  --network host \
  -v $CACHE/kit:/isaac-sim/kit/cache:rw \
  -v $CACHE/ov:/root/.cache/ov:rw \
  -v $CACHE/pip:/root/.cache/pip:rw \
  -v $CACHE/glcache:/root/.cache/nvidia/GLCache:rw \
  -v $CACHE/computecache:/root/.nv/ComputeCache:rw \
  -v $ARVR:/workspace/arvr:ro \
  nvcr.io/nvidia/isaac-sim:5.1.0 \
  bash -c '
    /isaac-sim/python.sh -m pip install websockets ar_contracts@/workspace/arvr/packages/ar-contracts &&
    /isaac-sim/python.sh /workspace/arvr/packages/isaac-bridge/run_twin_server.py --hz 30 --port 8766
  '
```

Container and every log/artifact path under this stays inside `sky`'s own
directory tree per the repo's shared-Spark rule (`CLAUDE.md`, "Dangerous
areas") — never write into `andrew/`, never touch containers you didn't
start.

Connect a client to `ws://<spark-host>:8766/twin/demo_room` and expect a
`TwinState` JSON frame every `1/hz` seconds, `objects[0].position_m`
actually falling and settling under gravity — not a sine wave.

## Known gaps (tracked, not silently skipped)

- No articulated robot in the scene (see above).
- No reset/loop — once the cube settles it just sits there; `ar_sim`'s
  `/twin` route resets every cycle so its demo runs indefinitely, this one
  doesn't yet.
- Not wired into `ar_backend` — a client has to point `TWIN_WS` at this
  process directly rather than going through `ar_backend`'s existing
  `/twin/{scene_id}` route. Folding it in (the same way `ar_backend/twin.py`
  already folded in `ar_sim` instead of shelling out to
  `mock_twin_server.py`) is the natural next step once Isaac's own startup
  latency (order 20s) is acceptable behind a request path.
