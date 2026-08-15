# arvr — STRUCT AR/XR spatial robotics interface (Feat 4 + Feat 5)

> Humans communicate physical intent spatially; Struct translates that
> intent into robot-compatible data and brings robot intelligence back into
> the physical environment as a digital twin.

Primary interface: smartphone AR. Optional: WebXR headset/controller.
Compute: NVIDIA DGX Spark. Simulation: NVIDIA Isaac Sim / OpenUSD.

See `../STRUCT_2.md` (or the pasted master plan) for the full spec this
directory implements. Start here for current status: [`STATE.md`](STATE.md).

## Layout

```
arvr/
├── packages/
│   └── ar-contracts/   frozen spatial schemas (SpatialFrame, TwinState, ...)
│   └── ar-datapipe/     normalize -> retarget (Pinocchio) -> verify (MuJoCo) -> export
│   └── ar-backend/       FastAPI Episodes + Scenes API (spec section 36-37), wraps ar-datapipe
│   └── isaac-bridge/    <- Spark-only, not yet built (needs Isaac Sim installed)
│   └── xr-web/          <- optional WebXR adapter, not yet built
├── apps/ios/            <- Andrew's phone app, not yet built
├── fixtures/
│   ├── ar-xr/            fixture pack — develop against this, don't wait on F3/Isaac
│   └── robot/            placeholder test-arm URDF (NOT the real robot)
├── tools/
│   ├── make_fixtures.py  regenerate the fixture pack deterministically
│   └── mock_twin_server.py  stream fake TwinState over WebSocket
├── tests/
└── docs/CONTRACTS.md     coordinate convention + frozen schema reference
```

## Quickstart

```bash
cd arvr
uv sync
make loop           # lint + test
make fixtures        # regenerate fixtures/ar-xr/*
make mock-twin       # ws://0.0.0.0:8765/twin/<scene_id> at 30 Hz
uv run uvicorn ar_backend.app:app --reload --port 8000  # Episodes + Scenes API
```

## Branch / ownership

Feature branches (`feat/ar-*`, `feat/xr-*`) merge into `feat/arvr-integration`
before the project-wide branch. See the master plan sections 6-13 for full
branch ownership; this directory does not enforce that, it's a process rule.
