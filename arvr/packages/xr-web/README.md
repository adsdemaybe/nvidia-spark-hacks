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

## Sort Teleop (`sort-teleop.html`) — red/blue ball sorting

The demo task the Quest build is actually for: six balls, three red and
three blue, picked up by pinching them and dropped into matching baskets,
while the SO-101 shadow follows the same task-space intent and the run is
recorded as a `HumanEpisode` and uploaded for retarget/verify. `RESET BALLS`
puts everything back, so it can be run repeatedly without a reload.

It is a **second entry point, not a replacement** for `spatial-teach.html`.
That page is the working button demo; this one shares every module that
matters with it (`hands.ts`, `xr.ts`, `xrCalibration.ts`, `xrHud.ts`,
`shadowHand.ts`, `shadowRobot.ts`, the recorder and the upload path), so the
two differ in the task and the scene, not in the plumbing. The rules live
outside the entry point — `sortTask.ts` (what counts as sorted), `grasp.ts`
(what counts as picked up), `sortSession.ts` (the per-frame wiring) — and
run headlessly under vitest with no headset, no GPU and no backend.

**Running it.** Same server, one different page:

```bash
npm run dev        # then open /sort-teleop.html
```

On a headset, everything in the section above applies unchanged — the HTTPS
requirement, `probe.html` first, binding the backend to `0.0.0.0`, and
turning hand tracking on in the headset's own settings. Nothing about the
networking differs; only the URL does. `HAND` offers `MOCK` always,
`WEBCAM` when the browser exposes `navigator.mediaDevices`, and `OPENXR`
only when `detectCapabilities()` reports an immersive session is available.

**The scene**, all in struct_world (Z-up, meters), with the robot base at
the origin and +X pointing away from it:

```text
tabletop   z = 0.14, x = 0.14 .. 0.38, y = -0.26 .. +0.26
baskets    red  (0.26, +0.175)   blue (0.26, -0.175)
           0.15m interior, 0.07m walls
balls      six, radius 0.03, in a 3x2 block down the center at
           x = 0.17 / 0.24 / 0.31, y = ±0.045, colors interleaved
```

Those numbers are measured, not chosen: `tools/so101_reach_envelope.py`
runs FK plus a position-only IK solve off the committed SO-101 URDF and puts
the usable tabletop at roughly 0.48m x 0.24m — smaller than the task spec's
0.60 x 0.45, which the arm cannot cover. `sortLayout.ts` carries the
residuals and the reasoning; don't retune the layout without re-running the
tool.

**Controls.** The DOM bar and the in-scene HUD render from one
`controlSpecs()` list, so they always offer the same actions — click them
flat, poke them with a fingertip in XR:

```text
ENTER XR      -> immersive session starts (HAND=OPENXR only)
PINCH x2      -> place the ROBOT BASE, then the RED BASKET, in your room
                 (~34cm apart -- that is this scene's own geometry; the HUD
                  shows the measured error and rejects a bad layout)
START DEMO    -> begins recording; the score readout goes live
FINISH        -> ends the episode and uploads it; the verdict appears
RESET BALLS   -> puts all six balls back (only outside a demo)
RECALIBRATE   -> in XR: throws the anchors away and re-places the workspace
EXIT XR       -> leaves the session
```

Flat on a desktop there is a `CALIBRATE` button instead of the two pinches.
It is the same honest stand-in `spatial-teach.html` uses off-headset — it
marks the scene calibrated so the rest of the flow is drivable, and it
solves nothing. The real two-anchor solve only happens in XR.

To pick a ball up, pinch within 5cm of its center — slightly more than its
own 3cm radius, because hand tracking is good to about a centimeter and a
ball visibly between your fingers should not refuse to be caught. A ball the
pinch would catch lights up before you close on it. A ball is scored only
once it is *released* inside a basket's interior volume: carrying one
through a basket on the way past does not count. The readout shows
`RED n/3` / `BLUE n/3` and `SORTED ✓` when every ball is home.

The uploaded episode carries the red basket as its goal point, because the
shared `TaskSpec` is a single goal — the real predicate (every ball in its
matching basket) lives in the recorded event stream. See STATE.md Round 10.

**Not run on a headset yet.** Same caveat as the section above: the flat
browser path has been driven for real, the task and grasp rules are
unit-tested, and nothing here has been through a Quest.

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
