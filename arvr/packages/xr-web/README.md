# packages/xr-web — Andrew — `feat/xr-web-adapter`

Working browser client covering **PLACE, TEACH, REPLAY, FOLLOW, TWIN,
CORRECT** — TypeScript / Three.js / WebXR, no backend framework, `npm run
dev` and open a browser. A tracked XR controller (or, without a headset,
mouse + WASD) is a `SpatialAdapter` — same downstream path either way
(`adapter.ts`), matching spec section 5/28. Ported into `arvr/` from
Andrew's independent `arxr/` implementation during the arvr/arxr
consolidation (see `../../STATE.md`) — originally built as *the* demoable
client since no phone/Mac was available; the phone remains the primary demo
device per spec, this stays the proven fallback/optional path.

## Quickstart

```bash
cd packages/xr-web
npm install
npm run dev        # http://localhost:5273
npm run test       # vitest
npm run typecheck
```

Fixtures are served straight from `../../fixtures/` (see `vite.config.ts`)
— no copies to drift out of sync with what the Python tests validate.

## Wiring to the backend

- **TWIN mode** connects to `ar_backend`'s `WS /twin/{scene_id}` (see
  `twinProvider.ts`) — "FIXTURE STREAM" replays the committed
  `fake_twin_state.jsonl`, "LIVE SERVER" is whatever `ar_backend` has wired
  behind that route (mock or `ar-sim`'s MuJoCo-backed twin). The UI always
  labels which one it's showing — never presents fixture state as live
  (spec section 82).
- **TEACH mode**'s `EpisodeRecorder` (`recorder.ts`) buffers locally, then
  uploads through `ar_backend`'s Episodes API (create → artifact → finish)
  on FINISH — see `episodeUpload.ts`. Survives a dropped connection because
  nothing is sent until the demo is complete (spec section 19).
- **CORRECT mode** posts each captured `CorrectionEvent` to
  `POST /xr/corrections`.
