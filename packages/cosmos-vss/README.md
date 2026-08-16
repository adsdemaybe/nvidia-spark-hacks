# cosmos-vss

F6 — standalone semantic video understanding for robotics demonstrations.

Independently watches a recorded (or live) human manipulation demo and produces a
`SemanticEpisode`: task type, objects and roles, a temporal phase timeline, spatial
relationships, and a success condition. It does **not** touch hand/object kinematics,
`HumanEpisode`, LeRobot export, or RL — see `COSMOS_VSS.md` at the repo root for the
full spec and the boundary this package must not cross.

## Backends

One interface (`VideoSemanticAnalyzer`), three implementations, selected by
`COSMOS_VSS_BACKEND` and never silently swapped at runtime:

- `vss` — primary path, through the NVIDIA VSS Real-Time VLM REST API.
- `cosmos` — fallback/debug path, direct Cosmos Reason NIM `/chat/completions`.
- `mock` — deterministic, offline, no GPU/NGC/VSS/internet. Used by the test suite.

## Quickstart

```bash
cd packages/cosmos-vss
uv sync
cp .env.example .env   # edit VSS_BASE_URL etc.

uv run pytest tests -q          # offline, mock backend, no GPU
uv run cosmos-vss doctor        # checks config + backend health
uv run cosmos-vss analyze demo.mp4 --episode-id episode_0007
uv run uvicorn cosmos_vss.app:app --port 8100   # debug UI at http://localhost:8100/
```

Artifacts land in `artifacts/semantic/<semantic_id>/`: `semantic.json` (validated),
`raw_response.json`, `request.json`, `provenance.json`.

To exercise the real `vss` or `cosmos` backend instead of `mock`, deploy VSS/Cosmos
separately (see `COSMOS_VSS.md` §15) and point `VSS_BASE_URL` / `COSMOS_BASE_URL` at it —
this package never vendors that deployment.
