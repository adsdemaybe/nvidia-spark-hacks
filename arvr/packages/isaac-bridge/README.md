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

## Platform quirk (aarch64/Spark-specific — read this before `docker run`)

Found empirically, not documented anywhere: this image's `ENTRYPOINT`
(`/isaac-sim/runheadless.sh`) checks `uname -m`, and on aarch64 (the
Spark's GB10) it just prints "Livestreaming is not supported on aarch64"
and `exec /bin/bash` — a **bare interactive shell**, completely ignoring
whatever `CMD`/trailing args you passed to `docker run`. It does *not* fall
back to `exec "$@"` the way you'd expect. Run non-interactively (no `-it`,
scripted/backgrounded) and this silently no-ops: the container drops to a
shell with no TTY attached, gets EOF, and exits — your command never runs,
with no error.

**Fix: override the entrypoint directly**, bypassing `runheadless.sh`
entirely:

```bash
docker run ... --entrypoint /isaac-sim/python.sh ... nvcr.io/nvidia/isaac-sim:5.1.0 /path/to/script.py
```

This is *not* what the recipe below originally used (`bash -c '...'`,
no `--entrypoint`) — that version only ever worked when run interactively
(`-it`), where a human manually retypes the commands inside the shell
`runheadless.sh` drops them into. Both recipes below use the fix.

## What's real and what's a placeholder right now

- **Real**: a `DynamicCuboid` under actual PhysX gravity/contact, in a real
  Isaac `World`. Its pose is read back from the stage every physics step —
  the same kind of proof `ar_sim`'s MuJoCo twin already gives for TEACH/
  CORRECT, just sourced from Isaac's simulator instead of MuJoCo's.
- **Real, as of `run_verify_server.py`**: the real SO-101 URDF, imported via
  `isaacsim.asset.importer.urdf`, driven joint-by-joint through a real
  trajectory (`isaacsim.core.prims.SingleArticulation`), with genuine PhysX
  dynamics between commanded and achieved pose — this is a *different*,
  independently-verified answer than MuJoCo's purely kinematic replay, not
  a duplicate of it (see `run_verify_server.py`'s own docstring for what
  this catches that MuJoCo's check can't, and what it still doesn't check
  — collision).
- **Still a placeholder**: `run_twin_server.py`'s `TwinState.robot.joint_positions`
  is still a fixed idle array — the live-streaming TwinState path hasn't
  been updated to use a real articulated robot the way the *batch verify*
  path now does. Wiring that in is the natural next increment for
  `run_twin_server.py` specifically.

## Running the TwinState streamer (from an SSH session, inside your own `ar-vr/sky/` tree)

```bash
CACHE=~/nvidia-spark-hacks/ar-vr/sky/artifacts/isaac-sim-cache
ARVR=~/nvidia-spark-hacks/ar-vr/sky/worktrees/<your-worktree>/arvr

# One-time per container: install the two pure-Python deps into Isaac's
# bundled interpreter. websockets needs a network-reachable pip index;
# ar_contracts installs from the mounted repo, no network needed.
docker run --name struct-ar-isaac-bridge --rm -it \
  --gpus all -e "ACCEPT_EULA=Y" -e "PRIVACY_CONSENT=Y" \
  --network host \
  --entrypoint bash \
  -v $CACHE/kit:/isaac-sim/kit/cache:rw \
  -v $CACHE/ov:/root/.cache/ov:rw \
  -v $CACHE/pip:/root/.cache/pip:rw \
  -v $CACHE/glcache:/root/.cache/nvidia/GLCache:rw \
  -v $CACHE/computecache:/root/.nv/ComputeCache:rw \
  -v $ARVR:/workspace/arvr:ro \
  nvcr.io/nvidia/isaac-sim:5.1.0 \
  -c '
    /isaac-sim/python.sh -m pip install websockets ar_contracts@/workspace/arvr/packages/ar-contracts &&
    /isaac-sim/python.sh /workspace/arvr/packages/isaac-bridge/run_twin_server.py --hz 30 --port 8766
  '
```

(`--entrypoint bash` here, not `/isaac-sim/python.sh` — this recipe runs two
shell steps, pip install then the script; the verify-server recipe below
runs one script directly, so it overrides straight to `python.sh` instead.)

Container and every log/artifact path under this stays inside `sky`'s own
directory tree per the repo's shared-Spark rule (`CLAUDE.md`, "Dangerous
areas") — never write into `andrew/`, never touch containers you didn't
start.

Connect a client to `ws://<spark-host>:8766/twin/demo_room` and expect a
`TwinState` JSON frame every `1/hz` seconds, `objects[0].position_m`
actually falling and settling under gravity — not a sine wave.

## Running the verify server (`spatial_providers.IsaacSimulationProvider`'s backend)

```bash
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
```

No extra `pip install` step needed — this server only imports stdlib +
`numpy`/`websockets` (already bundled in the Isaac Sim image) + Isaac's own
extensions, never `ar_contracts` (it speaks plain JSON on the wire, parsed
by hand — see the script's own docstring for why).

A client on a machine that can't reach the Spark's port directly (e.g. this
repo's own dev laptop, behind the same NAT that blocks WSL2↔LAN traffic
elsewhere in this project — see `arvr/STATE.md`) can reach it through an
SSH local port-forward instead of opening a firewall rule:
`ssh -N -L 8767:localhost:8767 spark`, then point
`ISAAC_VERIFY_WS_URL=ws://localhost:8767` at whichever machine runs the
tunnel's client side. **If testing from WSL specifically, run the SSH
tunnel *from inside WSL*** (not from Windows) — WSL2's own virtualized
network namespace doesn't share Windows' localhost port bindings, the same
NAT-isolation issue documented for the webcam/headset LAN work.

## Known gaps (tracked, not silently skipped)

- `run_twin_server.py`: no articulated robot in the scene yet (see above),
  no reset/loop (once the cube settles it just sits there — `ar_sim`'s
  `/twin` route resets every cycle so its demo runs indefinitely, this one
  doesn't yet), not wired into `ar_backend` (a client has to point
  `TWIN_WS` at this process directly rather than going through
  `ar_backend`'s existing `/twin/{scene_id}` route).
- `run_verify_server.py`: collision is not checked (`collision_valid`
  always `None`, an honest "not evaluated" per `VerificationChecks`'
  own contract, not a false pass) — Isaac's contact-reporting API
  (`PhysxContactReportAPI` per-body + a `RigidContactView`/`ContactSensor`)
  needs meaningfully more setup than MuJoCo's `data.ncon`; real, separate
  scope, not done this round. Joint drive gains use Isaac's own import
  defaults (never explicitly tuned via `ImportConfig.set_default_drive_strength`)
  — live-tested tracking error against the real SO-101's mock demo trajectory
  came in just over the 1cm tolerance (~1.0-1.2cm, tried both 1 and 3
  physics-settle-steps per frame, same order of magnitude either way) — a
  genuine PD-drive dynamics finding (Isaac's real physics catching a
  discrepancy MuJoCo's kinematic-only replay can't see by construction),
  not a bug to chase away by loosening the tolerance. Tuning drive
  stiffness is the natural next lever if closer agreement with MuJoCo is
  wanted.
