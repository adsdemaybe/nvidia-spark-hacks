# Hand-demonstration data collection

**What this is:** the half of STRUCT that records a human performing a
manipulation task with their own hands, and turns it into a dataset a
reinforcement-learning policy can train on.

A person picks a mug up off a table in front of a webcam. Every joint of
their hand, the mug's pose and velocity, and the events of the grasp are
recorded and exported as a LeRobot v3 dataset. That dataset is the product.

**Branch:** `feat/openxr-hand-provider`
**Entry point:** `packages/xr-web/mug-pickup.html`

---

## Where this sits in the wider project

```
text prompt
    │
    ├──────────────► CAD / PCB ──────► generated robot arm
    │                                          │
    │                                          │ (not built yet)
    ▼                                          ▼
human demonstrates          ────────►   RL training  ────────►  policy
with their own hands                                               │
(THIS DOCUMENT)                                                    ▼
                                                       simulation, viewed in a
                                                       Quest headset (next)
```

Two properties make this work without the rest of the project existing yet:

**The recording is robot-independent.** A `HumanEpisode` is what a person
did, not what a robot should do. It is recorded and stored before any
robot-specific conversion, so the same episodes can be compiled onto the
generated arm the moment it exists — and onto a different embodiment after
that. Nothing here waits on CAD.

**There is no robot in the capture scene.** The data is *for* a robot, but
the recording is of a hand and an object. Rendering an arm that is neither
being controlled nor retargeted onto adds nothing a demonstrator can use, so
the scene is a table and a mug.

---

## Running it

```bash
cd packages/xr-web
npm install
npm run dev            # then open /mug-pickup.html
```

The backend supplies the dataset export:

```bash
uv run --no-sync uvicorn ar_backend.app:app --port 8000
```

It is proxied through the dev server, so the page and the API share one
origin. That matters more than it sounds: a page served over https calling
`http://host:8000` is blocked by every browser as mixed content, and the
symptom is silent.

### The flow

```
START CAMERA   pick a device first if you have more than one
RECORD         begin an episode; the can/mug resets to its start pose
   ...         pick the mug up, move it, put it down
STOP & SAVE    uploads and exports; the dataset id appears in the readout
DISCARD        throw a bad take away instead of saving it
RESET CAN      put the mug back between takes
```

Each take starts from the same mug position on purpose. Episodes that each
begin somewhere different force a policy to learn that variation as signal.

### The readout

Live every frame, whether or not a hand is visible — an earlier version only
redrew inside the hand callback, so when nothing was detected the panel froze
and the app looked dead in exactly the case you needed to debug.

```
tracker   28 fps            detection is running
detected  right             which hand is in frame
grip      62%  (grabs at 55%)
hand xyz  0.31, 0.04, 0.12
mug  xyz  0.26, 0.00, 0.00
distance  9cm  (grab under 9cm)
DEPTH  ESTIMATED (single camera)
```

Those numbers separate failures that otherwise look identical: camera off,
camera on but no hand in frame, hand tracked but out of reach, and hand in
reach but not closed enough to grab.

---

## The scene

Right-handed, **Z-up**, meters. The tabletop is the `z = 0` plane.

```
tabletop     z = 0, x = -0.12 .. 0.46, y = -0.30 .. 0.30
mug          starts at (0.26, 0.00, 0.00), base-origin
mug size     89mm tall, 82mm across, handle out to 113.5mm
lift target  base clears z = 0.08
```

The mug is generated, not downloaded — `tools/make_mug_asset.py` builds it
with trimesh, deterministically (byte-identical on regeneration). Downloading
was rejected: the network is sandboxed and third-party assets carry licensing
questions this repo already takes seriously for the SO-101 meshes.

Its local origin is the **centre of its base**, so a position is where it
rests. The stand-in cylinder it replaced was centre-origin; the two are
indistinguishable until you look, and carrying the old convention over left
the mug hovering 4.5cm above the table.

---

## What gets recorded

One dataset row is **one instant in time**, with both hands and the object
grouped by timestamp — never one row per hand.

### Hand

| column | type | meaning |
|---|---|---|
| `action` / `observation.state` | float32[156] | 26 joint positions × 3 axes × 2 hands, left then right |
| `joints_valid` | float32[52] | 1.0 where that joint was genuinely measured |
| `observation.wrist_orientation_xyzw` | float32[8] | left then right wrist quaternion |
| `observation.pinch_aperture_m` | float32[2] | thumb-to-index distance in metres |

Per-joint orientations are deliberately excluded: they would add 208 floats
to encode mostly-redundant information, since consecutive joint positions
already give each bone's direction. Only roll is new, and roll has no meaning
until there is a target hand to map it onto. The wrist *is* kept — it is the
root of both chains and implied by nothing else.

Pinch is exported as raw aperture in metres rather than a 0–1 closure,
because that normalisation is gripper-specific calibration and this layer is
meant to stay embodiment-free.

### Object

| column | type | meaning |
|---|---|---|
| `observation.object_position_m` | float32[K×3] | struct_world metres |
| `observation.object_orientation_xyzw` | float32[K×4] | full rigid-body rotation |
| `object_valid` | float32[K] | 1.0 where measured at this instant |
| `observation.object_velocity_m_s` | float32[K×3] | linear velocity |
| `observation.object_velocity_dt_s` | float32[K] | interval that velocity was differenced over |
| `object_velocity_valid` | float32[K] | 1.0 where derived from two real measurements |

Column slot `i` belongs to `struct_object_order[i]` in `meta/info.json` — a
sorted set of ids, so meaning never depends on iteration order and nothing
assumes a single object.

Velocity is a **backward** finite difference against the last row that object
was actually measured on. Backward, not central, so an observation never
contains future information — a central difference would train a policy that
cannot run online.

### Missing data

Anything untracked is `NaN` **and** flagged invalid. Never zero-filled, never
silently held forward.

This is the single most important correctness property in the export. A zero
joint position is a real position — the origin — so zero-filling teaches a
policy that an occluded finger teleports to the base of the workspace.

### Events

`grasp_start`, `grasp_end`, `contact`, `task_start`, `task_finish`,
`tracking_lost`, each stamped with the object it concerns.

---

## Hand tracking

MediaPipe Hand Landmarker in the browser, both hands, producing the same
`HandFrame` contract the WebXR/headset path produces. Everything downstream —
recorder, exporter, dataset — is identical regardless of which provider ran.

### Grip detection

Grabbing is measured by **finger curl** (mean fingertip-to-wrist distance),
not thumb-to-index pinch.

This is not a preference. A pinch aperture describes a precision grip and
nothing else: wrap your hand around an 82mm mug and your thumb and index end
up ~7cm apart, which a pinch measure reports as a **fully open hand**. Grasp
detection built on pinch alone cannot detect the most natural way to pick up
a mug.

Engage at 55% closure, release at 35% — hysteretic, so a hand at the boundary
does not chatter between grabbed and dropped.

### Depth

The weakest axis, and worth understanding before trusting the data.

Depth comes from **apparent palm size** — wrist to middle-finger knuckle, in
normalised image units. A hand is a fixed-size object, so its projected size
falls as `1/distance`. Measured across the palm rather than to a fingertip
because that span does not change when the fingers curl, and they curl
exactly when someone grabs something; a fingertip measure would report a
lunge toward the camera at the worst possible moment.

MediaPipe's own landmark `z` is retained only as a fallback when the palm
landmarks are missing. It is hand-relative, unitless, and barely moves when
the whole hand translates — using it as the primary signal is what made depth
uncontrollable.

**This is still inference, not measurement.** Finger articulation is
trustworthy; absolute hand position in depth is approximate. `depth_quality`
stays `"estimated"` throughout and the app says so on screen.

**The best available fix is physical, not algorithmic.** A forward-facing
laptop webcam fundamentally cannot see toward/away motion. Move the camera:

- tilt the laptop screen so the webcam looks **down** at the desk, or
- use a phone as a virtual webcam (Iriun, Camo, EpocCam) placed **to the
  side**

Either turns depth into an axis the camera observes directly. A camera picker
is in the UI for the second option. Note that none of those apps expose a
depth channel — they stream 2D video, so the win is optics and **placement**,
not depth sensing.

If the camera is moved off-axis, the image→struct axis mapping in
`webcamHand.ts` must change to match.

---

## Layout

```
packages/xr-web/
  mug-pickup.html            the app
  src/
    mugPickupMain.ts         entry point: wiring, recording, UI
    mugPickupLayout.ts       scene constants, control volume
    mugPickupTask.ts         grasp/lift/place state + success predicate
    mugPickupScene.ts        table, mug, depth markers
    webcamHand.ts            MediaPipe -> HandFrame, both hands, depth
    hands.ts                 HandFrame, grip/pinch detection
    grasp.ts                 which object a closing hand takes
    shadowHand.ts            hand rendering
    humanEpisodeRecorder.ts  buffers a take
    humanEpisodeUpload.ts    create -> artifact -> export-human

packages/ar-datapipe/src/ar_datapipe/
    human_export.py          HumanEpisode -> LeRobot dataset

tools/
    make_mug_asset.py        generates the mug GLB
    so101_reach_envelope.py  measures the arm's reachable envelope
```

The task rules live outside the entry point on purpose: `mugPickupTask.ts`
decides what counts as a successful pickup, in code that runs headlessly
under vitest with no camera, no GPU and no backend. What enters the training
set is a deterministic geometric fact, never a judgement call.

---

## Validation

```bash
cd packages/xr-web && npm run typecheck && npx vitest run && npm run build
cd .. && uv run --no-sync ruff check packages tools tests
         uv run --no-sync pytest tests/ -q
```

Currently **212 vitest**, **178 pytest** (16 skipped: GPU/Isaac/device
markers), typecheck, build and ruff clean.

Always pass `--no-sync` to `uv run`. Without it uv re-syncs and intermittently
corrupts numpy on this OneDrive-backed venv.

---

## Known limitations

**Depth is inferred, not measured.** See above. The most important caveat for
anyone training on this data.

**MediaPipe gives 21 landmarks, WebXR gives 25.** There is no separate
metacarpal for the four fingers, so those joints are never emitted for webcam
frames and are marked invalid rather than interpolated.

**No angular velocity.** Object linear velocity is exported; angular is not,
because differencing quaternions needs a convention that is not decidable
without a target embodiment.

**Not verified on a headset.** The WebXR path exists and is unit-tested, but
has never run on real hardware.

**The exporter is not round-tripped through the real `lerobot` package** —
tests read it back with pyarrow only.

---

## Next

1. **Collect episodes.** More demonstrations beats higher-fidelity
   demonstrations for early RL. Record the depth caveat in provenance so
   whoever trains knows what they have.
2. **Move the camera off-axis** and retune the mapping — the single biggest
   quality improvement available.
3. **Swap in the generated arm** when CAD produces one. `RobotProvider`
   already has the switch point; `GeneratedRobotProvider` raises
   `NotImplementedError` today rather than silently doing nothing.
4. **Show the simulation in the headset.** `twinProvider.ts` and
   `isaac-bridge/run_twin_server.py` already stream Isaac state as
   `TwinState`; `RobotState.joint_positions` is a variable-length tuple, so
   it already fits a hand or an arm with any joint count.
