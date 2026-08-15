/**
 * STRUCT browser spatial client (ar-xr-plan.md 28).
 *
 * The same modes as the phone app -- PLACE, TEACH, REPLAY, FOLLOW, TWIN,
 * CORRECT -- driven by whatever spatial input is available. With a headset that
 * is a tracked controller; without one it is the mouse and WASD. Both go
 * through a SpatialAdapter, so the downstream path is identical either way,
 * which is the claim in ar-xr-plan.md 5 made concrete.
 *
 * This is the optional client. It exists to prove hardware optionality and to
 * be developable without a Mac; the phone remains the primary demo device.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { DesktopMockAdapter, XRControllerAdapter, structToWebxr } from "./adapter";
import { CameraUnavailable, fitBackground, startCamera, type CameraFeed } from "./camera";
import { readHand, wristTarget, type HandFrame } from "./hands";
import { describe as describeSession, detectCapabilities, startBestSession, type SessionKind } from "./xr";
import type { SpatialAdapter } from "./adapter";
import { ArticulatedArm, REACH_M, SHOULDER_HEIGHT, reachStatus } from "./arm";
import type { CorrectionEvent, CorrectionResponse, CorrectionVerification, TwinState, Vec3 } from "./contracts";
import { DEFAULT_FOLLOW_DISTANCE_M, SCHEMA_VERSION } from "./contracts";
import { IDENTITY_ALIGNMENT, applyAlignment, reprojectionErrorM, solveAlignment, type Alignment } from "./alignment";
import { gripperEvents, loadEpisode, path, poseAt, type LoadedEpisode } from "./episode";
import { uploadEpisode, type EpisodeVerdict } from "./episodeUpload";
import { FollowSession } from "./followSession";
import { EpisodeRecorder } from "./recorder";
import {
  FIXTURES_BASE,
  LAYOUT,
  Trail,
  buildEnvironment,
  loadScene,
  makeTargetLine,
  placeAtStruct,
  updateLine,
} from "./scene";
import { followTarget } from "./spatial";
import {
  MockTwinStateProvider,
  WebSocketTwinStateProvider,
  type TwinStateProvider,
} from "./twinProvider";

const MODES = ["PLACE", "TEACH", "REPLAY", "FOLLOW", "TWIN", "CORRECT"] as const;
type Mode = (typeof MODES)[number];

// ar_backend's own port (see ../../ar-backend/src/ar_backend/app.py) — one
// server for REST (episodes/scenes/corrections) and the twin stream.
const API_BASE = "http://127.0.0.1:8000";
const TWIN_WS = "ws://127.0.0.1:8000/twin/demo_room";
const FIXTURE_TWIN = `${FIXTURES_BASE}fake_twin_state.jsonl`;

const app = document.getElementById("app")!;
const modesEl = document.getElementById("modes")!;
const controlsEl = document.getElementById("controls")!;
const readoutEl = document.getElementById("readout")!;
const provenanceEl = document.getElementById("provenance")!;

// ---------------------------------------------------------------- renderer --
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.xr.enabled = true;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const FLAT_BACKGROUND = new THREE.Color(0x14171c);
scene.background = FLAT_BACKGROUND;
const environment = buildEnvironment(scene);

// ------------------------------------------------------------------- XR --
// AR-first per the locked decision in master plan §0. The button reports what
// the runtime actually granted rather than promising AR and delivering VR.
let sessionKind: SessionKind = "flat";
let handTrackingGranted = false;
let referenceSpace: XRReferenceSpace | undefined;

const xrButton = document.createElement("button");
xrButton.id = "xr-entry";
xrButton.textContent = "CHECKING XR…";
xrButton.disabled = true;
document.body.appendChild(xrButton);

void detectCapabilities().then((caps) => {
  xrButton.disabled = caps.best === "flat";
  xrButton.textContent =
    caps.best === "immersive-ar" ? "ENTER PASSTHROUGH AR"
    : caps.best === "immersive-vr" ? "ENTER VR (NO PASSTHROUGH)"
    : "NO XR ON THIS DEVICE";
  if (caps.best !== "immersive-ar") {
    xrButton.title = caps.notes.join("\n") || "immersive-ar unavailable";
  }
});

xrButton.addEventListener("click", () => {
  void (async () => {
    try {
      const started = await startBestSession();
      sessionKind = started.kind;
      handTrackingGranted = started.handTracking;

      // In passthrough the background must be transparent, or the app paints
      // over the real room and the whole point is lost.
      scene.background = started.kind === "immersive-ar" ? null : FLAT_BACKGROUND;
      environment.grid.visible = started.kind !== "immersive-ar";

      await renderer.xr.setSession(started.session);
      referenceSpace = renderer.xr.getReferenceSpace() ?? undefined;

      started.session.addEventListener("end", () => {
        sessionKind = "flat";
        handTrackingGranted = false;
        referenceSpace = undefined;
        scene.background = FLAT_BACKGROUND;
        environment.grid.visible = true;
      });
    } catch (error) {
      xrButton.textContent = `XR FAILED: ${String(error).slice(0, 40)}`;
    }
  })();
});

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.01, 100);
camera.position.set(2.6, 2.4, 3.4);

const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0.5, -0.4);
orbit.enableDamping = true;
orbit.update();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// -------------------------------------------------------------------- arm --
const arm = new ArticulatedArm();
let robotBase: Vec3 = LAYOUT["robot"] ?? [0.15, -0.7, 0];
arm.placeBase(robotBase);
scene.add(arm.root);

/** The reach envelope PLACE shows: a sphere about the shoulder. */
const envelope = new THREE.Mesh(
  new THREE.SphereGeometry(REACH_M, 32, 24),
  new THREE.MeshBasicMaterial({
    color: 0x00d4aa,
    transparent: true,
    opacity: 0.06,
    depthWrite: false,
    side: THREE.BackSide,
  }),
);
const envelopeWire = new THREE.LineSegments(
  new THREE.WireframeGeometry(new THREE.SphereGeometry(REACH_M, 20, 12)),
  new THREE.LineBasicMaterial({ color: 0x00d4aa, transparent: true, opacity: 0.14 }),
);
scene.add(envelope, envelopeWire);

// ------------------------------------------------------------------ markers --
const human = marker(0x00d4aa, 0.09);
const followMarker = marker(0xffb347, 0.07);
const endEffector = marker(0xe06c75, 0.045);
const correctionGhost = marker(0xc678dd, 0.055);
const originalGhost = marker(0x565f89, 0.045);
const grabMarker = marker(0x98c379, 0.04);
const releaseMarker = marker(0xe5c07b, 0.04);
scene.add(human, followMarker, endEffector, correctionGhost, originalGhost, grabMarker, releaseMarker);

const followLine = makeTargetLine();
const correctionLine = makeTargetLine(0xc678dd);
scene.add(followLine, correctionLine);

const liveTrail = new Trail();
const demoTrail = new Trail(0x61afef);
scene.add(liveTrail.line, demoTrail.line);

function marker(color: number, radius: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 20, 16),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.35 }),
  );
  mesh.castShadow = true;
  mesh.visible = false;
  return mesh;
}

// --------------------------------------------------------------------- state --
let mode: Mode = "TWIN";
let humanPosition: Vec3 = [1.6, 0.6, 0.0];
let humanYaw = Math.PI;
let followDistance = DEFAULT_FOLLOW_DISTANCE_M;
let latestTwin: TwinState | undefined;
let provider: TwinStateProvider | undefined;
let sceneObjects: Map<string, THREE.Object3D> | undefined;

let episode: LoadedEpisode | undefined;
let replaySeconds = 0;
let replayPlaying = true;

let latestVerdict: EpisodeVerdict | undefined;
let verdictPending = false;

// spec section 17 lists CALIBRATE first among TEACH's required controls.
// There's no real AR anchor system yet to calibrate against (see §49) — this
// records that the human explicitly confirmed a starting pose before
// recording is allowed to start, rather than silently assuming one.
let calibrated = false;

let followSession: FollowSession | undefined;
let followSessionState: "stopped" | "following" | "paused" = "stopped";
let latestFollowTwin: TwinState | undefined;

// ------------------------------------------------------------- alignment --
// Twin Alignment v0 (spec section 49): deterministic manual anchors, not
// automatic relocalization. Two known struct_world points; the human taps
// where each actually is in the room, and that solves T_struct_to_ar.
const ANCHOR_ROBOT_BASE_STRUCT: Vec3 = LAYOUT["robot"] ?? [0.15, -0.7, 0];
// Table's near-left corner (TABLE_SIZE 1.20x0.80 in ar_sim/scene_mjcf.py,
// centered at LAYOUT["table"]) — any fixed, known struct_world point works;
// a corner is just easy for a human to point at precisely.
const ANCHOR_TABLE_CORNER_STRUCT: Vec3 = [-0.2, -0.4, 0];

let twinAlignment: Alignment = IDENTITY_ALIGNMENT;
let anchorReprojectionErrorM: number | undefined;
let anchorStep: "idle" | "awaiting_robot_base" | "awaiting_table_corner" = "idle";
let tappedRobotBaseStruct: Vec3 | undefined;

const ORIGINAL_TARGET: Vec3 = [0.45, -0.05, 1.0];
let correctedTarget: Vec3 = [0.45, -0.05, 1.18];
let corrections: CorrectionEvent[] = [];
let lastCorrectionVerification: CorrectionVerification | undefined;

const adapter: SpatialAdapter = navigator.xr ? new XRControllerAdapter() : new DesktopMockAdapter();
const recorder = new EpisodeRecorder({
  taskId: "cube_to_bin",
  deviceType: adapter.deviceType,
  inputType: adapter.inputType,
});

const keys = new Set<string>();
addEventListener("keydown", (e) => keys.add(e.key.toLowerCase()));
addEventListener("keyup", (e) => keys.delete(e.key.toLowerCase()));

// ---------------------------------------------------------------- picking --
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
let dragging = false;

renderer.domElement.addEventListener("pointerdown", (e) => {
  if (mode === "TWIN" && anchorStep !== "idle") {
    handleAnchorTap(e);
    return;
  }
  if (mode !== "PLACE" && mode !== "CORRECT") return;
  dragging = true;
  orbit.enabled = false;
  handlePointer(e);
});
addEventListener("pointerup", () => {
  if (dragging && mode === "CORRECT") recordCorrection();
  dragging = false;
  orbit.enabled = true;
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (dragging) handlePointer(e);
});

function handlePointer(event: PointerEvent): void {
  pointer.set(
    (event.clientX / innerWidth) * 2 - 1,
    -(event.clientY / innerHeight) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);

  if (mode === "PLACE") {
    // Drop the robot wherever the floor was clicked.
    const hit = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(floorPlane, hit)) return;
    robotBase = [-hit.z, -hit.x, 0];
    arm.placeBase(robotBase);
  } else {
    // Drag the ghost in a horizontal plane at its current height.
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -correctedTarget[2]);
    const hit = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(plane, hit)) return;
    correctedTarget = [-hit.z, -hit.x, correctedTarget[2]];
  }
}

/**
 * One tap = one anchor (spec section 49). Two taps total: robot base, then
 * table corner, in that order — after the second, solve and store the
 * alignment. A tap, not a drag, because an anchor is a single point in
 * time, not something to adjust live.
 */
function handleAnchorTap(event: PointerEvent): void {
  pointer.set(
    (event.clientX / innerWidth) * 2 - 1,
    -(event.clientY / innerHeight) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  const hit = new THREE.Vector3();
  if (!raycaster.ray.intersectPlane(floorPlane, hit)) return;
  const tappedStruct: Vec3 = [-hit.z, -hit.x, 0];

  if (anchorStep === "awaiting_robot_base") {
    tappedRobotBaseStruct = tappedStruct;
    anchorStep = "awaiting_table_corner";
  } else if (anchorStep === "awaiting_table_corner" && tappedRobotBaseStruct) {
    const anchorA = { structPosition: ANCHOR_ROBOT_BASE_STRUCT, tappedPosition: tappedRobotBaseStruct };
    const anchorB = { structPosition: ANCHOR_TABLE_CORNER_STRUCT, tappedPosition: tappedStruct };
    twinAlignment = solveAlignment(anchorA, anchorB);
    anchorReprojectionErrorM = reprojectionErrorM(twinAlignment, anchorB);
    anchorStep = "idle";
    tappedRobotBaseStruct = undefined;
  }
  renderControls();
}

function recordCorrection(): void {
  const event: CorrectionEvent = {
    schema_version: SCHEMA_VERSION,
    task_id: "cube_to_bin",
    timestamp_ns: nowNs(),
    original_target: { position_m: ORIGINAL_TARGET },
    corrected_target: { position_m: correctedTarget },
    reason: "collision_avoidance",
  };
  corrections = [...corrections, event];
  lastCorrectionVerification = undefined;
  console.info("CorrectionEvent captured", event);
  fetch(`${API_BASE}/xr/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  })
    .then((res) => res.json() as Promise<CorrectionResponse>)
    .then((body) => {
      lastCorrectionVerification = body.verification;
      renderControls();
    })
    .catch((error: unknown) => console.warn("failed to post CorrectionEvent:", error));
}

/** spec section 70 DoD item 5: surface whether the last captured correction
 * is actually reachable, not just stored. */
function correctionVerificationLines(verification: CorrectionVerification | undefined): string[] {
  if (corrections.length === 0) return [];
  if (!verification) return ["verifying..."];
  if (!verification.checked) return [`verification unavailable (${verification.reason ?? "unknown"})`];
  if (verification.reachable) {
    return [`✓ reachable  (pose error ${(verification.pose_error ?? 0).toFixed(4)})`];
  }
  return [`⚠ ${verification.reason ?? "not reachable"}`];
}

// ---------------------------------------------------------------------- HUD --
for (const m of MODES) {
  const button = document.createElement("button");
  button.textContent = m;
  button.setAttribute("aria-pressed", String(m === mode));
  button.onclick = () => setMode(m);
  button.dataset["mode"] = m;
  modesEl.appendChild(button);
}

function setMode(next: Mode): void {
  mode = next;
  for (const button of modesEl.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset["mode"] === next));
  }
  if (next === "REPLAY") replaySeconds = 0;
  renderControls();
}

function renderControls(): void {
  controlsEl.replaceChildren();
  const add = (label: string, onClick: () => void, className = ""): void => {
    const button = document.createElement("button");
    button.textContent = label;
    button.className = className;
    button.onclick = onClick;
    controlsEl.appendChild(button);
  };

  // Always available: the physical world is not a per-mode concern.
  add(cameraFeed ? "CAMERA OFF" : "CAMERA ON", () => void toggleCamera());

  if (mode === "TEACH") {
    if (!calibrated) {
      add("CALIBRATE", () => {
        calibrated = true;
        renderControls();
      }, "rec");
    } else if (recorder.isRecording) {
      add("GRAB", () => {
        recorder.grab(nowNs());
        placeAtStruct(grabMarker, effectorPosition());
        grabMarker.visible = true;
      });
      add("RELEASE", () => {
        recorder.release(nowNs());
        placeAtStruct(releaseMarker, effectorPosition());
        releaseMarker.visible = true;
      });
      add("FINISH", () => {
        const captured = recorder.finish(nowNs());
        const frames = recorder.frames();
        console.info("episode captured", captured, frames.length, "frames");
        latestVerdict = undefined;
        verdictPending = true;
        renderControls();
        uploadEpisode(API_BASE, captured, frames, captured.events, LAYOUT["bin"] ?? [0.6, -0.7, 0.55])
          .then((verdict) => { latestVerdict = verdict; })
          .catch((error: unknown) => {
            latestVerdict = {
              episode_id: captured.episode_id,
              status: "upload_failed",
              tracking_error_m: null,
              task_success: null,
              dataset_id: null,
              rejection_reason: String(error),
            };
          })
          .finally(() => { verdictPending = false; renderControls(); });
      }, "rec");
      add("CANCEL", () => {
        recorder.cancel(nowNs());
        liveTrail.clear();
        grabMarker.visible = releaseMarker.visible = false;
        renderControls();
      });
    } else {
      add("START DEMO", () => {
        liveTrail.clear();
        grabMarker.visible = releaseMarker.visible = false;
        latestVerdict = undefined;
        recorder.start(nowNs());
        renderControls();
      }, "rec");
    }
  } else if (mode === "FOLLOW") {
    if (followSessionState === "stopped") {
      add("START FOLLOW", () => void startFollow(), "rec");
    } else {
      add(followSessionState === "paused" ? "RESUME" : "PAUSE", () => {
        followSessionState = followSessionState === "paused" ? "following" : "paused";
        renderControls();
      });
      add("STOP", () => {
        followSession?.stop();
        followSession = undefined;
        followSessionState = "stopped";
        latestFollowTwin = undefined;
        renderControls();
      });
    }
    add("−0.25 m", () => { followDistance = Math.max(0.25, followDistance - 0.25); });
    add("+0.25 m", () => { followDistance += 0.25; });
  } else if (mode === "TWIN") {
    add("LIVE SERVER", () => void connectTwin("live"));
    add("FIXTURE STREAM", () => void connectTwin("fixture"));
    if (anchorStep === "idle") {
      add("SET ANCHORS", () => {
        anchorStep = "awaiting_robot_base";
        renderControls();
      });
    } else {
      add(
        anchorStep === "awaiting_robot_base" ? "TAP ROBOT BASE…" : "TAP TABLE CORNER…",
        () => {},
        "rec",
      );
    }
  } else if (mode === "REPLAY") {
    add(replayPlaying ? "PAUSE" : "PLAY", () => { replayPlaying = !replayPlaying; renderControls(); });
    add("RESTART", () => { replaySeconds = 0; });
  } else if (mode === "PLACE") {
    add("RESET ROBOT", () => {
      robotBase = LAYOUT["robot"] ?? [0.15, -0.7, 0];
      arm.placeBase(robotBase);
    });
  } else if (mode === "CORRECT") {
    add("RESET GHOST", () => { correctedTarget = [0.45, -0.05, 1.18]; });
    add("RAISE", () => { correctedTarget = [correctedTarget[0], correctedTarget[1], correctedTarget[2] + 0.05]; });
    add("LOWER", () => { correctedTarget = [correctedTarget[0], correctedTarget[1], Math.max(0.05, correctedTarget[2] - 0.05)]; });
  }
}

// -------------------------------------------------------------------- twin --
async function connectTwin(kind: "live" | "fixture"): Promise<void> {
  provider?.stop();
  provider =
    kind === "live"
      ? new WebSocketTwinStateProvider(TWIN_WS)
      : await MockTwinStateProvider.load(FIXTURE_TWIN);

  provider.start(
    (state) => { latestTwin = state; },
    (p) => {
      provenanceEl.textContent = p.label;
      provenanceEl.setAttribute("data-live", String(p.live));
      provenanceEl.setAttribute("data-connected", String(p.connected));
    },
  );
}

// ------------------------------------------------------------------ follow --
async function startFollow(): Promise<void> {
  try {
    const session = await FollowSession.start(API_BASE);
    session.connect(
      (state) => { latestFollowTwin = state; },
      () => {
        // Backend-initiated close (e.g. server restart) — reflect it in the
        // UI rather than silently going stale.
        if (followSessionState !== "stopped") {
          followSessionState = "stopped";
          followSession = undefined;
          renderControls();
        }
      },
    );
    followSession = session;
    followSessionState = "following";
  } catch (error) {
    console.warn("failed to start follow session:", error);
    followSessionState = "stopped";
  }
  renderControls();
}

// -------------------------------------------------------------------- loop --
function nowNs(): number {
  return Math.round(performance.now() * 1e6);
}

function effectorPosition(): Vec3 {
  return [humanPosition[0], humanPosition[1], 0.95];
}

function stepHuman(dt: number): void {
  const speed = 1.2 * dt;
  if (keys.has("a")) humanYaw += 1.6 * dt;
  if (keys.has("d")) humanYaw -= 1.6 * dt;
  const forward: Vec3 = [Math.cos(humanYaw), Math.sin(humanYaw), 0];
  const step = keys.has("w") ? speed : keys.has("s") ? -speed : 0;
  humanPosition = [
    humanPosition[0] + forward[0] * step,
    humanPosition[1] + forward[1] * step,
    0,
  ];
}

function hideAll(...objects: THREE.Object3D[]): void {
  for (const o of objects) o.visible = false;
}

const clock = new THREE.Clock();

// ---------------------------------------------------------------- camera --
// The physical world as pixels. Untracked -- see camera.ts. Kept separate from
// the XR passthrough path so the HUD can always say which one is in play.
let cameraFeed: CameraFeed | undefined;
let cameraError: string | undefined;

async function toggleCamera(): Promise<void> {
  if (cameraFeed) {
    cameraFeed.stop();
    cameraFeed = undefined;
    if (sessionKind !== "immersive-ar") scene.background = FLAT_BACKGROUND;
    environment.grid.visible = sessionKind !== "immersive-ar";
    renderControls();
    return;
  }

  try {
    cameraError = undefined;
    cameraFeed = await startCamera();
    fitBackground(cameraFeed.texture, cameraFeed.aspect, innerWidth / innerHeight);
    scene.background = cameraFeed.texture;
    // The real floor is in the video; a synthetic grid over it reads as fake.
    environment.grid.visible = false;
  } catch (error) {
    cameraError = error instanceof CameraUnavailable ? error.message : String(error);
  }
  renderControls();
}

addEventListener("resize", () => {
  if (cameraFeed) {
    fitBackground(cameraFeed.texture, cameraFeed.aspect, innerWidth / innerHeight);
  }
});

/** Hands seen this frame, when the runtime granted hand input. */
let liveHands: HandFrame[] = [];

function readHands(frame: XRFrame | null): void {
  liveHands = [];
  if (!frame || !referenceSpace || !handTrackingGranted) return;
  for (const source of frame.session.inputSources) {
    const hand = readHand(frame, source, referenceSpace);
    if (hand) liveHands.push(hand);
  }
}

renderer.setAnimationLoop((_time, frame) => {
  const dt = Math.min(clock.getDelta(), 0.1);
  orbit.update();
  readHands(frame ?? null);
  stepHuman(dt);

  hideAll(
    human, followMarker, endEffector, correctionGhost, originalGhost,
    followLine, correctionLine, envelope, envelopeWire,
  );
  liveTrail.line.visible = false;
  demoTrail.line.visible = false;

  const shoulderWorld = structToWebxr.position([
    robotBase[0], robotBase[1], robotBase[2] + SHOULDER_HEIGHT,
  ]);
  envelope.position.set(...shoulderWorld);
  envelopeWire.position.copy(envelope.position);

  if (mode === "PLACE") {
    envelope.visible = envelopeWire.visible = true;
  }

  if (mode === "FOLLOW") {
    const humanQuat: [number, number, number, number] = [
      0, 0, Math.sin(humanYaw / 2), Math.cos(humanYaw / 2),
    ];
    const target = followTarget(
      { position_m: humanPosition, orientation_xyzw: humanQuat }, followDistance,
    );
    human.visible = followMarker.visible = followLine.visible = true;
    placeAtStruct(human, humanPosition);
    placeAtStruct(followMarker, target);
    updateLine(followLine, humanPosition, target);

    if (followSessionState === "following") {
      followSession?.send({
        schema_version: SCHEMA_VERSION,
        timestamp_ns: nowNs(),
        human_pose: { position_m: humanPosition, orientation_xyzw: humanQuat },
        desired_follow_distance_m: followDistance,
        follow_target: { position_m: target },
      });
    }
    // The chase stand-in's response (ar_backend/follow.py) — a *different*
    // robot position from `target` above: that's where it should ideally
    // be, this is where the (placeholder) navigation actually put it.
    if (latestFollowTwin) {
      const base = latestFollowTwin.objects.find((o) => o.id === "robot_base");
      if (base) {
        robotBase = base.position_m;
        arm.placeBase(robotBase);
      }
    }
  }

  if (mode === "TEACH") {
    const effector = effectorPosition();
    endEffector.visible = true;
    liveTrail.line.visible = true;
    placeAtStruct(endEffector, effector);

    if (recorder.isRecording) {
      recorder.capture(
        adapter.toSpatialFrame(
          { position: structToWebxr.position(effector), orientation: [0, 0, 0, 1], trigger: 0 },
          nowNs(),
        ),
      );
      liveTrail.push(effector);
    }
  }

  if (mode === "REPLAY" && episode) {
    if (replayPlaying) replaySeconds = (replaySeconds + dt) % episode.durationSeconds;
    const frame = poseAt(episode, replaySeconds);
    endEffector.visible = demoTrail.line.visible = true;
    placeAtStruct(endEffector, frame.position_m);
  }

  if (mode === "CORRECT") {
    correctionGhost.visible = originalGhost.visible = correctionLine.visible = true;
    placeAtStruct(originalGhost, ORIGINAL_TARGET);
    placeAtStruct(correctionGhost, correctedTarget);
    updateLine(correctionLine, ORIGINAL_TARGET, correctedTarget);
  }

  if (latestTwin) {
    arm.setJoints(latestTwin.robot.joint_positions);
    for (const object of latestTwin.objects) {
      const mesh = sceneObjects?.get(object.id.replace(/_\d+$/, ""));
      if (mesh) placeAtStruct(mesh, applyAlignment(twinAlignment, object.position_m));
    }
    // The static scene (table/cube/bin GLBs) needs the same alignment as
    // the live objects, or a calibrated Twin would show the robot's cube
    // correctly placed relative to a table that's still sitting wherever
    // the un-aligned model puts it — the two would visibly disagree.
    if (twinAlignment !== IDENTITY_ALIGNMENT) {
      for (const [id, mesh] of sceneObjects ?? []) {
        const structPos = LAYOUT[id];
        if (structPos) placeAtStruct(mesh, applyAlignment(twinAlignment, structPos));
      }
    }
  }

  readoutEl.textContent = readout();
  renderer.render(scene, camera);
});

/**
 * Where the "real world" in view is coming from, stated plainly.
 *
 * A webcam backdrop and tracked passthrough look similar in a screenshot and
 * are not remotely the same claim (§48, §66). The HUD says which one it is.
 */
function worldSource(): string {
  if (sessionKind === "immersive-ar") return "PASSTHROUGH AR — TRACKED";
  if (cameraError) return `CAMERA UNAVAILABLE — ${cameraError}`;
  if (cameraFeed) return `WEBCAM BACKDROP — NOT TRACKED (${cameraFeed.label})`;
  return "NONE — virtual only, no physical environment";
}

function readout(): string {
  const p = (v: Vec3): string => v.map((n) => n.toFixed(2)).join(", ");
  const lines = [
    `MODE       ${mode}`,
    `session    ${describeSession(sessionKind, handTrackingGranted)}`,
    `world      ${worldSource()}`,
    `input      ${adapter.deviceType}`,
  ];

  for (const hand of liveHands) {
    const wrist = wristTarget(hand);
    lines.push(
      `${hand.handedness.padEnd(10)} ${Object.keys(hand.joints).length} joints` +
        `  pinch ${hand.pinchApertureM?.toFixed(3) ?? "--"} m` +
        `  grip ${hand.gripper.toFixed(2)}` +
        (wrist ? `  wrist ${wrist.position.map((n) => n.toFixed(2)).join(", ")}` : ""),
    );
  }

  if (mode === "PLACE") {
    const cube = reachStatus(robotBase, [0.3, 0.1, 0.78]);
    const bin = reachStatus(robotBase, [0.6, -0.7, 0.34]);
    lines.push(
      `base       ${p(robotBase)}`,
      `reach      ${REACH_M.toFixed(2)} m from shoulder`,
      `cube       ${cube.reachable ? "REACHABLE ✓" : "OUTSIDE WORKSPACE ✗"} (${cube.distance.toFixed(2)} m)`,
      `bin        ${bin.reachable ? "REACHABLE ✓" : "OUTSIDE WORKSPACE ✗"} (${bin.distance.toFixed(2)} m)`,
      "",
      "click the floor to reposition",
    );
  }
  if (mode === "FOLLOW") {
    const target = followTarget(
      { position_m: humanPosition, orientation_xyzw: [0, 0, Math.sin(humanYaw / 2), Math.cos(humanYaw / 2)] },
      followDistance,
    );
    // Distance from the human to where the robot actually is (once a
    // session is connected), not to the ideal target — which would always
    // equal `desired` by construction and say nothing about tracking error.
    const trackedPos = followSessionState !== "stopped" ? robotBase : target;
    const actual = Math.hypot(
      humanPosition[0] - trackedPos[0],
      humanPosition[1] - trackedPos[1],
      humanPosition[2] - trackedPos[2],
    );
    lines.push(
      `human      ${p(humanPosition)}`,
      `target     ${p(target)}`,
      `desired    ${followDistance.toFixed(2)} m`,
      `actual     ${actual.toFixed(2)} m`,
      `error      ${Math.abs(actual - followDistance).toFixed(2)} m`,
      `session    ${followSessionState.toUpperCase()}`,
    );
  }
  if (mode === "TEACH") {
    if (!calibrated) {
      lines.push("NOT CALIBRATED — press CALIBRATE to begin");
    }
    lines.push(
      recorder.isRecording ? "RECORDING  ●" : "idle",
      `frames     ${recorder.frameCount}`,
      `duration   ${recorder.durationSeconds.toFixed(1)} s`,
    );
    if (verdictPending) {
      lines.push("", "verifying…");
    } else if (latestVerdict) {
      const accepted = latestVerdict.status === "accepted";
      lines.push(
        "",
        accepted ? "DEMONSTRATION ACCEPTED" : `DEMONSTRATION ${latestVerdict.status.toUpperCase()}`,
        `tracking   ${latestVerdict.tracking_error_m?.toFixed(4) ?? "-"} m`,
        `dataset    ${latestVerdict.dataset_id ?? "-"}`,
      );
      if (latestVerdict.rejection_reason) lines.push(`reason     ${latestVerdict.rejection_reason}`);
    }
  }
  if (mode === "REPLAY") {
    if (!episode) lines.push("episode    loading…");
    else {
      const events = gripperEvents(episode)
        .map((e) => `${e.type}@${e.at.toFixed(1)}s`)
        .join("  ");
      lines.push(
        "REPLAYING DEMONSTRATION",
        `task       ${episode.meta.task_id}`,
        `frames     ${episode.frames.length}`,
        `t          ${replaySeconds.toFixed(2)} / ${episode.durationSeconds.toFixed(2)} s`,
        `events     ${events}`,
      );
    }
  }
  if (mode === "CORRECT") {
    const moved = Math.hypot(
      correctedTarget[0] - ORIGINAL_TARGET[0],
      correctedTarget[1] - ORIGINAL_TARGET[1],
      correctedTarget[2] - ORIGINAL_TARGET[2],
    );
    lines.push(
      `original   ${p(ORIGINAL_TARGET)}`,
      `corrected  ${p(correctedTarget)}`,
      `delta      ${moved.toFixed(3)} m`,
      `captured   ${corrections.length}`,
      "",
      "drag the ghost, release to capture",
      "",
      ...correctionVerificationLines(lastCorrectionVerification),
    );
  }
  if (mode === "TWIN") {
    if (anchorStep === "awaiting_robot_base") {
      lines.push("", "tap where the ROBOT BASE actually is");
    } else if (anchorStep === "awaiting_table_corner") {
      lines.push("", "tap where the TABLE CORNER actually is");
    } else if (twinAlignment !== IDENTITY_ALIGNMENT) {
      lines.push(
        "",
        "TWIN ALIGNED (spec section 49)",
        `reprojection error  ${((anchorReprojectionErrorM ?? 0) * 100).toFixed(1)} cm` +
          ((anchorReprojectionErrorM ?? 0) < 0.05 ? " ✓" : " ⚠ over 5cm target"),
      );
    } else {
      lines.push("", "NOT ALIGNED — press SET ANCHORS (digital twin claim needs this, spec §65-66)");
    }
  }
  if (latestTwin) {
    lines.push(
      `scene      ${latestTwin.scene_id}`,
      `task       ${latestTwin.task?.id ?? "-"} ${latestTwin.task?.status ?? ""}`,
      `joints     ${latestTwin.robot.joint_positions.map((j) => j.toFixed(2)).join(" ")}`,
    );
  }
  return lines.join("\n");
}

// ------------------------------------------------------------------- boot --
loadScene()
  .then((loaded) => {
    sceneObjects = loaded.objects;
    // The robot GLB is a static stand-in; the articulated arm replaces it.
    loaded.objects.get("robot")?.removeFromParent();
    scene.add(loaded.root);
    readoutEl.textContent = "scene loaded";
    return connectTwin("fixture");
  })
  .catch((error: unknown) => {
    readoutEl.textContent = `scene failed to load:\n${String(error)}`;
    provenanceEl.textContent = "SCENE UNAVAILABLE";
  });

loadEpisode(FIXTURES_BASE)
  .then((loaded) => {
    episode = loaded;
    for (const position of path(loaded)) demoTrail.push(position);
  })
  .catch((error: unknown) => console.warn("episode unavailable:", error));

renderControls();
