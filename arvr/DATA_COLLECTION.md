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
uv run --no-sync uvicorn ar_backend.app:app --port 8000 --reload
```

**Use `--reload`.** Without it uvicorn serves whatever it imported at startup
forever, so a route added since then is simply absent and the client gets
`404 {"detail":"Not Found"}` from a route that is plainly present on disk. This
has already cost one debugging session: `create` and `artifact` answered 200
while `export-human` 404'd, because the running process predated it. If an
export 404s, check the live route table before suspecting the code:

```bash
curl -s http://127.0.0.1:8000/openapi.json | grep -o "/spatial/episodes[^\"]*"
```

On Windows a second uvicorn can bind a port that is already in use rather than
failing, so "I started it again" does not guarantee the old one is gone.

It is proxied through the dev server, so the page and the API share one
origin. That matters more than it sounds: a page served over https calling
`http://host:8000` is blocked by every browser as mixed content, and the
symptom is silent.

### The flow

```
START CAMERA   pick a device and a PLACEMENT first — see Depth, below
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

placement OVERHEAD
palm span 0.284  (near 0.43 / far 0.16)
DEPTH  ESTIMATED — height (Z) only; X/Y measured
```

Those numbers separate failures that otherwise look identical: camera off,
camera on but no hand in frame, hand tracked but out of reach, and hand in
reach but not closed enough to grab.

`palm span` is the raw measurement the estimated axis is derived from, shown
next to the two constants it is compared against, so that calibration is a
measurement rather than a feeling — see Depth.

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

Exporting writes **two** artifacts from the same recording:

| artifact | where | for |
|---|---|---|
| LeRobot v3 parquet | `arvr/data/lerobot/human/<task>/` | training a policy |
| SQLite | `arvr/datasets/human_demos.sqlite` | **sharing** — commit and push it |

`arvr/data/` is gitignored, so the parquet copy cannot be handed to a
collaborator by pushing it. The SQLite file can, needs only the standard
library to read, and accumulates every episode into one file. See
`arvr/datasets/README.md`. It is written *first*, so a machine without pyarrow
still keeps the recording instead of losing the take to a 503.

The rest of this section describes the parquet layout.

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
trustworthy; absolute distance from the lens is approximate. `depth_quality`
stays `"estimated"` throughout and the app says so on screen.

**The best available fix is physical, not algorithmic.** A camera cannot see
motion along its own optical axis, so the fix is to point that axis somewhere
the task does not need. That is what **placement** is, and the picker beside
the camera picker sets it.

Placement is a **tilt angle**, not a mode: how far the camera is tilted down
from horizontal. The axis mapping is derived from a single rotation at that
angle (`cameraBasis` in `webcamHand.ts`), so every angle in between works.

| tilt | what it is | image y | palm span |
|---|---|---|---|
| `0°` | screen upright, facing you | all height | all reach |
| `45°` (default) | laptop lid open, looking down at the desk | height **and** reach | reach **and** height |
| `90°` | straight down — a phone on a stand | all reach | all height |

**45° is the default because that is what a laptop actually does.** A hinge
cannot reach 90°; fully open it looks down maybe 30–50°. The earlier build had
only two modes, `0°` and `90°`, and defaulted to `90°` — so at a real lid angle
half of "up" was being assigned to the wrong axis. The demonstrator saw
vertical control inverted *and* the hand model tipped over, which are the same
error seen twice.

**Pick the entry that matches your camera.** If vertical feels inverted or the
hand looks tipped, the tilt setting disagrees with the physical camera; at the
right angle the hand looks right and moves right simultaneously, which is the
one thing to check.

**90° is still the best placement available**, because looking straight down
makes both axes the hand reaches along directly measured and demotes the
estimate to the lift. It needs a phone on a stand rather than a laptop lid — a
phone as a virtual webcam (Iriun, Camo, EpocCam) also beats laptop optics
badly. None of those apps expose a depth channel; the win is optics and
geometry, not depth sensing.

**The setting must match where the camera actually is.** Placement decides
which struct axis each image axis drives, so a mismatch does not fail loudly:
tracking looks correct and records the wrong geometry.

### The camera sees you mirrored

A camera pointed at you sees your right hand on its left. `resolveHandSide` has
always compensated for that — it is why a mirrored preview flips the handedness
label — but for a long time the flip was applied to the **label and nothing
else**, leaving positions in the camera's mirrored frame while the labels were
in yours. Two symptoms, one cause:

- Reaching right moved the hand left.
- The rendered hand was **inside out**: with image-x unflipped, the
  camera→struct map is a *reflection* (negative determinant), and a reflection
  turns a right hand into a left hand.

The second is invisible to per-axis testing, because a reflection is correct on
every axis taken one at a time. Only the sign of a scalar triple product
catches it, which is what the chirality tests assert. Both mappings —
`placeInControlVolume` and `worldLandmarksToStructJoints` — now take the same
`mirrored` flag, and they must always agree: flipping one without the other
puts the fingers on the wrong side of the wrist.

### Scale: the hand is the ruler

Hand position is **metric**. Apparent palm span and image position are measured
in the same normalized units, so their ratio converts one to the other — a palm
spanning a quarter of the frame means the frame is four palms wide. No camera
calibration, no field-of-view constant: the focal length cancels. It also
scales with distance for free, because a hand that moves further away shrinks
on screen at the same rate the frame widens.

This is what makes **two hands land the real distance apart**. Before it, the
wrist anchor was interpolated across the control volume, so the full frame
width always became the box's width regardless of how much desk the camera saw
— while the finger geometry came through as true metres. Every hand rendered
full size with the gap between them squashed to fit the box: hands 40cm apart
landed about 18cm apart, and the hands looked too big for the space between
them.

Two consequences worth knowing:

- **`MUG_CONTROL_VOLUME` is a reach limit, not a scale factor.** It is sized to
  the table now, because a natural two-handed reach spans 40–50cm and a
  narrower box clips the recording silently, exactly when the hands are
  furthest apart. Widening it no longer changes how far a given hand motion
  travels.
- **Depth is the exception.** Apparent size is already spent estimating
  distance, so it cannot also calibrate it. That axis spans a fixed
  `DEPTH_TRAVEL_M` (35cm) rather than the box, so widening the box does not
  make depth twice as sensitive.

### Constants calibrated from real data

A 36-second two-handed recording (2488 frames) was measured and four constants
changed as a result. Re-run `tools/` analysis against a fresh episode if the
setup or the demonstrator changes.

| what | was | now | evidence |
|---|---|---|---|
| handedness flip | on | **off** | right hand was on the +X side of left in **0.0%** of 1215 paired instants |
| palm ruler | fixed 10cm | **measured per hand** | this demonstrator's palm is 9.4cm — a 6% scale error |
| `GRIP_OPEN_M` / `GRIP_CLOSED_M` | 0.135 / 0.075 | **0.18 / 0.07** | 91% of frames saturated: closure was effectively a boolean |
| held-mug floor | none | **tabletop** | 5.4% of object samples were below z=0, worst −5cm |

The handedness one is worth understanding, because it hid for a long time.
`mirroredPreview` was driving two different decisions: where a hand is *placed*
and what it is *called*. Only the first is real — the preview is mirrored by a
CSS transform, and CSS cannot change the pixels MediaPipe reads, so the
landmarker always gets the raw frame and its label needs no correction.

While the geometry was also unflipped, both errors pointed the same way and the
result looked self-consistent: a "left" label on the left of the screen, in a
world that was mirrored end to end. Every frame self-reported a plausible
handedness, so no unit test could catch it — it took two hands in one real
recording, where the arms would have had to be crossed for 36 seconds.

`LANDMARKER_INPUT_MIRRORED` is now the single place that decision lives. Change
it only if a provider genuinely pre-flips its frames.

### Retuning the estimated axis

Placement changes *which* axis is estimated; it does not remove the estimate.
That axis still comes from apparent palm size, and the near/far span it is
scaled against depends on how far your camera is from your hands. The overhead
defaults assume a lens about 50cm above the desk.

The HUD's `palm span` line is the calibration instrument:

1. Rest your hand on the table — that reading is your `far`.
2. Hold it at lift height — that reading is your `near`.
3. If they don't bracket the numbers in brackets, edit `CAMERA_PLACEMENTS`
   in `webcamHand.ts`.

Symptom of skipping this: the reading sits pinned at one extreme, so the
estimated axis barely moves however you move your hand.

### Changing the axis mapping

There is now exactly one place: **`cameraBasis(tiltDeg, mirrored)`**. It
returns the struct-space directions of the camera's image axes, and both
consumers derive from it:

- `placeInControlVolume` — where the wrist anchor lands.
- `worldLandmarksToStructJoints` — the finger geometry around it. MediaPipe's
  world landmarks are camera-relative, so they rotate with the camera exactly
  as the anchor does.

They used to be two hand-maintained axis tables, and keeping them in sync was
the source of every sign bug in this file. Deriving both from one rotation also
makes mirroring **unrepresentable**: a rotation has determinant +1, so the
rendered hand can no longer come out inside-out whatever angle is chosen. The
tests assert that directly (`cameraBasis` orthonormal + right-handed, and hand
chirality preserved at every tilt).

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
    webcamHand.ts            MediaPipe -> HandFrame, both hands, placement + depth
    hands.ts                 HandFrame, grip/pinch detection
    grasp.ts                 which object a closing hand takes
    shadowHand.ts            hand rendering
    humanEpisodeRecorder.ts  buffers a take
    humanEpisodeUpload.ts    create -> artifact -> export-human

packages/ar-datapipe/src/ar_datapipe/
    human_export.py          HumanEpisode -> LeRobot dataset (training)
    sqlite_export.py         HumanEpisode -> one shareable file (pushing)

datasets/
    human_demos.sqlite       the committable dataset; see its README

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

Currently **239 vitest**, **198 pytest** (16 skipped: GPU/Isaac/device
markers), typecheck, build and ruff clean.

`tests/test_end_to_end_mug_pickup.py` is the one that matters most: it drives
the three HTTP calls the client really makes, in order, against a real app, and
asserts the demonstration comes back out of the SQLite file with the lift
intact. Every piece of the export was individually green while the app as a
whole could not save a take, because nothing exercised the chain.

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

1. **Collect episodes**, camera overhead. More demonstrations beats
   higher-fidelity demonstrations for early RL. Confirm the palm-span
   calibration against your own desk first (see Depth) — it is one edit, and
   it is the difference between an axis that moves and one that is pinned.
2. **Record placement in provenance.** `HumanEpisodeMetadata` carries
   `hand_provider` but not placement, so a dataset does not currently say
   which axes were measured and which were inferred — the one thing whoever
   trains on it most needs to know. It is a frozen wire contract shared with
   `human_export.py`, so adding the field is a coordinated change across both
   halves, not a client-side edit.
3. **Swap in the generated arm** when CAD produces one. `RobotProvider`
   already has the switch point; `GeneratedRobotProvider` raises
   `NotImplementedError` today rather than silently doing nothing.
4. **Show the simulation in the headset.** `twinProvider.ts` and
   `isaac-bridge/run_twin_server.py` already stream Isaac state as
   `TwinState`; `RobotState.joint_positions` is a variable-length tuple, so
   it already fits a hand or an arm with any joint count.
