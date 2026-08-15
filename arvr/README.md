# arvr — STRUCT AR/XR spatial robotics interface (Feat 4 + Feat 5)

> Humans communicate physical intent spatially; Struct translates that
> intent into robot-compatible data and brings robot intelligence back into
> the physical environment as a digital twin.

Primary interface: smartphone AR. Working now: a browser client
(`packages/xr-web/`) covering all six modes. Compute: NVIDIA DGX Spark.
Simulation: MuJoCo locally (`packages/ar-sim/`), NVIDIA Isaac Sim / OpenUSD
on the Spark eventually.

See [`../ar-xr-plan.md`](../ar-xr-plan.md) for the full spec this directory
implements — every "spec section N" comment in this codebase cites that
file, not `STRUCT_2.md` (the whole-project master plan, a different,
broader document covering all 5 feats). Start here for current status:
[`STATE.md`](STATE.md).

## Layout

```
arvr/
├── packages/
│   ├── ar-contracts/    frozen spatial schemas (SpatialFrame, TwinState, ...)
│   ├── ar-datapipe/      normalize -> retarget (Pinocchio) -> verify (MuJoCo) -> export
│   ├── ar-sim/            live MuJoCo physics twin (grasp, gravity, pick-place)
│   ├── ar-backend/        FastAPI: Episodes, Scenes, Twin (WS), Corrections
│   ├── xr-web/            browser client — PLACE/TEACH/REPLAY/FOLLOW/TWIN/CORRECT
│   └── isaac-bridge/     <- Spark-only, not yet built (needs Isaac Sim running)
├── apps/ios/             <- Andrew's phone app, not yet built
├── fixtures/
│   ├── ar-xr/             fixture pack incl. real GLB assets — develop against this
│   └── robot/             placeholder test-arm URDF (NOT the real robot; see ar-sim/README.md)
├── tools/
│   ├── make_fixtures.py   regenerate the fixture pack deterministically
│   └── mock_twin_server.py  stream fake TwinState over WebSocket (fixture fallback)
├── tests/
└── docs/CONTRACTS.md      coordinate convention + frozen schema reference
```

## Quickstart

```bash
cd arvr
uv sync
make loop           # lint + test
make fixtures        # regenerate fixtures/ar-xr/*
uv run uvicorn ar_backend.app:app --reload --port 8000
# separately:
cd packages/xr-web && npm install && npm run dev   # http://localhost:5273
```

`ar-datapipe`/`ar-sim`/`ar-backend`'s MuJoCo/Pinocchio dependencies are
Linux-only (see `packages/ar-datapipe/README.md`) — `uv sync` still
succeeds on Windows, those tests self-skip via `pytest.importorskip`. Run
them on WSL or the Spark for real coverage.

## Branch / ownership

Feature branches (`feat/ar-*`, `feat/xr-*`) merge into `feat/arvr-integration`
before the project-wide branch. See the master plan sections 6-13 for full
branch ownership; this directory does not enforce that, it's a process rule.

`packages/xr-web/` and `packages/ar-sim/` were originally built
independently by Andrew under a separate `arxr/` tree and consolidated
here — see `STATE.md`'s "arvr/arxr consolidation" section for what that
involved.
