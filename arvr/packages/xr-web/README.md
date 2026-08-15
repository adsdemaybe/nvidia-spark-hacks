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

## Running Spatial Teach on a headset (`HAND=openxr`)

The headset path is implemented and unit-tested but has **not** been run on
real hardware. Everything below is the checklist for the first person who
straps one on; nothing here is a claim that it has already worked.

**1. Find out what the headset actually grants.** Open `probe.html` in the
headset browser first. It reports whether `immersive-ar`, `immersive-vr` and
`hand-tracking` exist on that device. A Quest 3 should give AR passthrough;
a Quest 1's browser is frozen and may only give VR, in which case the app
degrades to `immersive-vr` and says so in the HUD rather than pretending.

**2. Serve over HTTPS.** WebXR needs a secure context, and `localhost` is
exempt only for the machine running the server — a headset on the LAN is
not. `npm run dev` already runs HTTPS with a self-signed cert (`host: true`
binds all interfaces); accept the certificate warning once in the headset.
Do **not** set `VITE_NO_SSL=1` for the address a headset connects to.

**3. Let the headset reach the backend.** Start it bound to all interfaces:

```bash
uv run uvicorn ar_backend.app:app --host 0.0.0.0 --port 8000
```

The client derives the backend host from wherever the page was loaded, so a
headset hitting `https://<lan-ip>:5273/spatial-teach.html` will look for the
backend at `<lan-ip>:8000`. If the backend runs inside WSL, its NAT hides it
from the LAN — a `netsh interface portproxy` rule plus a matching firewall
rule is what bridges that (see STATE.md; the firewall half has never been
confirmed).

**4. Turn hand tracking on in the headset's own settings.** The app reports
`NO HAND TRACKING` when the runtime did not grant it — that is a device
setting, not something the page can fix.

**5. The flow.** `HAND` → `OPENXR`, then:

```text
ENTER XR   -> immersive session starts, controls move into the scene
PINCH x2   -> place the robot base, then the button, in your real room
              (they must be ~38cm apart -- that is the robot's own geometry;
               the HUD shows the measured error and rejects a bad layout)
START DEMO -> poke the in-scene button
FINISH     -> poke it; the verdict appears on the HUD, you stay in XR
EXIT XR    -> leaves the session
```

Calibration is what makes any of this mean something: without it the
headset's tracking origin is treated as the robot's base, and the SO-101's
~35cm of reach is asked to cover ~1.3m. See `xrCalibration.ts`.

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
