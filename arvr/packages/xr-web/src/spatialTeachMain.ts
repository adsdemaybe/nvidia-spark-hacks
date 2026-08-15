/**
 * STRUCT Spatial Teach — Shadow Robot Spatial Demonstration Pipeline spec
 * section 43 (Minimal UI), the new entry point for the pivot away from the
 * mode-based app (main.ts, untouched). ROBOT/ASSET/HAND/SIMULATOR selectors,
 * CALIBRATE, START DEMO, FINISH -- a single flow, not a mode switcher.
 *
 * Tested and verified end-to-end with HAND = mock (MockHandProvider) and
 * HAND = webcam. HAND = openxr is the headset path: xr.ts/hands.ts read the
 * hand, xrCalibration.ts maps the room onto the robot's workspace, and
 * xrHud.ts puts the controls somewhere a human wearing a headset can actually
 * reach them. See STATE.md for exactly what has and has not been confirmed on
 * real hardware.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  PinchLatch,
  pinchPoint,
  pokePoint,
  preferredInputSource,
  readHand,
  wristTarget,
  type HandFrame,
} from "./hands";
import {
  describe as describeSession,
  detectCapabilities,
  startBestSession,
  wantsTransparentBackground,
  type StartedSession,
} from "./xr";
import { HumanEpisodeRecorder } from "./humanEpisodeRecorder";
import { uploadHumanEpisode, type SpatialEpisodeVerdict } from "./humanEpisodeUpload";
import { LiveRetargetSession, type RobotShadowState } from "./liveRetargetSession";
import { MockHandProvider } from "./mockHand";
import { ShadowHand } from "./shadowHand";
import { ShadowRobot } from "./shadowRobot";
import { WebcamHandProvider } from "./webcamHand";
import { buildEnvironment, placeAtStruct } from "./scene";
import {
  ANCHOR_ERROR_LIMIT_M,
  ANCHOR_SEPARATION_M,
  XrCalibration,
} from "./xrCalibration";
import { XrHud } from "./xrHud";
import {
  ASSET_WORLD_POSE,
  ASSET_WORLD_POSITION,
  DEFAULT_GOAL_M,
  ROBOT_BASE_STRUCT,
} from "./spatialTeachLayout";
import type { Vec3 } from "./contracts";

// A hardcoded 127.0.0.1 only reaches the backend when the browser runs on
// the same machine as it. A physical headset is a separate device on the
// LAN -- its own 127.0.0.1 means the headset itself, nothing is listening
// there. Deriving the host from wherever the page was actually loaded from
// (the same LAN IP the vite dev server's own HTTPS URL uses, per
// vite.config.ts's `host: true`) makes this work from either. The backend
// also needs to be started with --host 0.0.0.0 for a headset to reach it
// at all (see STATE.md) -- this fixes only the client side of that.
const API_BASE = `http://${location.hostname}:8000`;

// The fixture scene's struct_world placement now lives in
// spatialTeachLayout.ts -- xrCalibration.ts needs the same numbers, because
// the two points a human pinches to calibrate ARE the robot base and the
// asset. One copy, imported by both.

const app = document.getElementById("app")!;
const selectorsEl = document.getElementById("selectors")!;
const controlsEl = document.getElementById("controls")!;
const readoutEl = document.getElementById("readout")!;
const calibrationEl = document.getElementById("calibration")!;
const webcamPanelEl = document.getElementById("webcam-panel")!;
const webcamPanelLabelEl = webcamPanelEl.querySelector(".label")!;

// ---------------------------------------------------------------- renderer --
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.xr.enabled = true;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const SCENE_BACKGROUND = new THREE.Color(0x14171c);
scene.background = SCENE_BACKGROUND;
const { grid } = buildEnvironment(scene);

/**
 * Everything that lives in struct_world, under one transform.
 *
 * On a desktop this is the identity and nothing changes. In a headset it is
 * what calibration moves: the human pinches where the robot's base belongs on
 * their real desk, and the whole workspace -- robot, button, stand -- goes
 * there. Lights stay outside it (they light the room, not the workspace).
 */
const worldRoot = new THREE.Group();
scene.add(worldRoot);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.01, 100);
// Framed for the real SO-101's much smaller scale (Track A) -- the old
// framing (1.6, 1.4, 2.0) was tuned for a ~1m-reach placeholder arm and a
// 0.53m-tall button; everything now lives within roughly a 0.4m radius of
// the origin.
camera.position.set(0.6, 0.5, 0.75);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0.2, 0.15, 0.0);
orbit.enableDamping = true;
orbit.update();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// The shadow hand deliberately stays outside worldRoot: it visualizes where
// the human's real hand is, in the room, and moving it with the workspace
// would peel it off the hand it is supposed to be drawn on.
const shadowHand = new ShadowHand();
scene.add(shadowHand.root);

const shadowRobot = new ShadowRobot();
shadowRobot.placeBase(ROBOT_BASE_STRUCT);
worldRoot.add(shadowRobot.root);

const hud = new XrHud();
scene.add(hud.root);

// A plain stand under the button -- Milestone 1 has no scene manifest / room
// reconstruction (fixture scene only, spec section 41), so without this the
// button asset just hangs in empty space at its world height with nothing
// visually supporting it. A visual-only prop, not a modeled InteractableAsset.
const stand = new THREE.Mesh(
  new THREE.CylinderGeometry(0.02, 0.025, ASSET_WORLD_POSITION[2], 16),
  new THREE.MeshStandardMaterial({ color: 0x4b5263, roughness: 0.8, metalness: 0.1 }),
);
placeAtStruct(stand, [ASSET_WORLD_POSITION[0], ASSET_WORLD_POSITION[1], ASSET_WORLD_POSITION[2] / 2]);
worldRoot.add(stand);

void new GLTFLoader().loadAsync("/spatial-training/assets/button/asset.glb").then((gltf) => {
  gltf.scene.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      (child as THREE.Mesh).material = new THREE.MeshStandardMaterial({
        color: 0xe5c07b, roughness: 0.6, metalness: 0.1,
      });
    }
  });
  placeAtStruct(gltf.scene, ASSET_WORLD_POSITION);
  worldRoot.add(gltf.scene);
});

/** The desktop loop. Named so an ended XR session can restore it. */
function flatFrame(): void {
  orbit.update();
  renderer.render(scene, camera);
}
renderer.setAnimationLoop(flatFrame);

// ------------------------------------------------------------------- state --
type HandProviderKind = "mock" | "openxr" | "webcam";
let handProviderKind: HandProviderKind = "mock";
let mockProvider: MockHandProvider | undefined;
let webcamProvider: WebcamHandProvider | undefined;
let calibrated = false;
let recorder: HumanEpisodeRecorder | undefined;
let liveSession: LiveRetargetSession | undefined;
let demoActive = false;
let framesSeen = 0;
let lastHandTracked = false;
let lastReachM: number | undefined;
let lastIkStatus: RobotShadowState["ik_status"] | undefined;
let latestVerdict: SpatialEpisodeVerdict | undefined;
let verdictPending = false;

// ----------------------------------------------------------------- WebXR --
let xrReferenceSpace: XRReferenceSpace | undefined;
let xrSessionInfo: StartedSession | undefined;
const calibration = new XrCalibration();
const pinchLatch = new PinchLatch();
/** Set when both anchors are placed but the layout did not pass the gate --
 * kept so the HUD can show the human the number instead of just refusing. */
let calibrationFailureM: number | undefined;

void detectCapabilities().then((caps) => {
  if (caps.best !== "flat") {
    const opt = document.createElement("option");
    opt.value = "openxr";
    opt.textContent = "OPENXR";
    handSelect.appendChild(opt);
  }
});

// -------------------------------------------------------------------- HUD --
function addSelector(label: string, id: string, options: string[]): HTMLSelectElement {
  const wrapper = document.createElement("label");
  wrapper.textContent = label;
  const select = document.createElement("select");
  select.id = id;
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt;
    el.textContent = opt.toUpperCase();
    select.appendChild(el);
  }
  wrapper.appendChild(select);
  selectorsEl.appendChild(wrapper);
  return select;
}

const robotSelect = addSelector("Robot", "robot-select", ["so101"]);
const assetSelect = addSelector("Asset", "asset-select", ["button_01"]);
const handSelect = addSelector("Hand", "hand-select", ["mock"]);
addSelector("Simulator", "sim-select", ["mujoco"]);
handSelect.addEventListener("change", () => {
  handProviderKind = handSelect.value as HandProviderKind;
  // Picking OPENXR has to surface ENTER XR, which is now a step of its own.
  renderControls();
});

// A webcam is a normal laptop peripheral, not a special-permission device --
// unlike XR, no async capability probe is needed, just checking the API
// exists (matches how "openxr" is only offered when XR is actually usable).
if (navigator.mediaDevices) {
  const opt = document.createElement("option");
  opt.value = "webcam";
  opt.textContent = "WEBCAM";
  handSelect.appendChild(opt);
}

void fetch(`${API_BASE}/robots`)
  .then((r) => r.json() as Promise<Array<{ robot_id: string }>>)
  .catch(() => []);
void fetch(`${API_BASE}/assets`)
  .then((r) => r.json() as Promise<Array<{ asset_id: string }>>)
  .catch(() => []);

function addButton(label: string, onClick: () => void, className = ""): HTMLButtonElement {
  const button = document.createElement("button");
  button.textContent = label;
  if (className) button.className = className;
  button.onclick = onClick;
  controlsEl.appendChild(button);
  return button;
}

/** True once an immersive session is running. */
function inXr(): boolean {
  return xrSessionInfo !== undefined;
}

/** In a headset, calibration is the real two-anchor solve; on a desktop it
 * stays the honest "the human confirmed a starting pose" stand-in it always
 * was (there is no AR anchor system to solve against on a flat page). */
function isCalibrated(): boolean {
  return inXr() ? calibration.isCalibrated : calibrated;
}

interface ControlSpec {
  id: string;
  label: string;
  action: () => void;
  disabled?: boolean;
  className?: string;
}

/**
 * The flow's controls, described once.
 *
 * Both the DOM control bar and the in-scene HUD render from this list, so a
 * headset and a desktop can never drift into offering different actions --
 * which is precisely how the openxr path ended up unable to reach FINISH.
 */
function controlSpecs(): ControlSpec[] {
  const specs: ControlSpec[] = [];

  if (handProviderKind === "openxr" && !inXr()) {
    specs.push({ id: "enter-xr", label: "ENTER XR", action: () => void enterXr() });
  } else if (!isCalibrated()) {
    specs.push(
      inXr()
        ? { id: "calibrating", label: "PINCH TO PLACE", action: () => {}, disabled: true }
        : { id: "calibrate", label: "CALIBRATE", action: calibrate },
    );
  } else if (!demoActive) {
    specs.push({ id: "start", label: "START DEMO", action: () => void startDemo() });
  } else {
    specs.push({ id: "finish", label: "FINISH", action: () => void finishDemo(), className: "rec" });
  }

  if (inXr()) {
    if (isCalibrated() && !demoActive) {
      specs.push({ id: "recalibrate", label: "RECALIBRATE", action: recalibrate });
    }
    specs.push({ id: "exit-xr", label: "EXIT XR", action: () => void exitXr() });
  }

  if (latestVerdict?.status === "accepted") {
    specs.push({
      id: "accepted",
      label: "ADD TO TRAINING SET ✓",
      action: () => {},
      disabled: true,
      className: "accepted",
    });
  }
  return specs;
}

function renderControls(): void {
  const specs = controlSpecs();

  controlsEl.replaceChildren();
  for (const spec of specs) {
    addButton(spec.label, spec.action, spec.className ?? "").disabled = spec.disabled ?? false;
  }

  // A disabled control is a status line, not something to poke at in mid-air.
  hud.setButtons(
    specs.filter((s) => !s.disabled).map((s) => ({ id: s.id, label: s.label })),
  );

  renderReadout();
}

/** Route an in-scene HUD press back through the same handler the DOM button
 * would have called. */
function pressControl(id: string): void {
  controlSpecs().find((spec) => spec.id === id && !spec.disabled)?.action();
}

function renderReadout(): void {
  calibrationEl.dataset["calibrated"] = String(isCalibrated());
  calibrationEl.textContent = isCalibrated() ? "CALIBRATED" : "NOT CALIBRATED";

  const lines: string[] = [
    `robot      ${robotSelect.value}`,
    `asset      ${assetSelect.value}`,
    `hand       ${handSelect.value}`,
  ];

  if (inXr() && xrSessionInfo) {
    lines.push(
      "",
      describeSession(xrSessionInfo.kind, xrSessionInfo.handTracking),
      `dom overlay     ${xrSessionInfo.domOverlay ? "granted" : "not granted"}`,
    );
    // Hand tracking is the whole input path. A session that started without
    // it is not a session that can teach anything, and saying so beats
    // leaving the human waving at a robot that will never move.
    if (!xrSessionInfo.handTracking) {
      lines.push("NO HAND TRACKING — enable it in the headset's settings");
    }
    lines.push(...calibrationLines());
  }
  // Spec section 49: provider interchangeability (same HandFrame) does not
  // mean sensor fidelity is equal -- say so plainly, not just in code.
  if (handProviderKind === "webcam") {
    const status = webcamProvider?.getStatus();
    lines.push(
      "HAND SOURCE     LAPTOP WEBCAM",
      "MODE            SCREEN CONTROL",
      "DEPTH           ESTIMATED",
    );
    if (status) {
      lines.push(
        `fps             ${status.resultFps.toFixed(0)}`,
        `handedness      ${status.handedness ?? "…"}`,
        `pinch           ${status.pinchActive ? "closed" : "open"}`,
      );
    }
  }
  if (demoActive) {
    lines.push(
      "",
      "● RECORDING",
      `HAND TRACKING   ${lastHandTracked ? "✓" : "…"}`,
      `ROBOT IK        ${lastIkStatus ?? "…"}`,
      `TARGET          ${assetSelect.value}`,
      `FRAMES          ${framesSeen}`,
    );
  } else if (verdictPending) {
    lines.push("", "verifying…");
  } else if (latestVerdict) {
    const ok = latestVerdict.status === "accepted";
    lines.push(
      "",
      `RETARGET/VERIFY  ${ok ? "PASS" : "FAIL"}`,
      ok ? "DEMONSTRATION ACCEPTED" : `REJECTED: ${latestVerdict.rejection_reason ?? "unknown"}`,
      latestVerdict.dataset_id ? `dataset  ${latestVerdict.dataset_id}` : "",
    );
  }
  const text = lines.filter((l) => l !== "").join("\n");
  readoutEl.textContent = text;
  hud.setText(text);
}

/** What calibration has to say for itself, including the measured error when
 * it failed -- a bare "NOT CALIBRATED" gives the human nothing to correct. */
function calibrationLines(): string[] {
  if (calibration.isCalibrated) {
    return [
      `CALIBRATED      anchor error ${((calibration.anchorErrorM ?? 0) * 100).toFixed(1)}cm`,
      `reach           ${lastReachM === undefined ? "…" : `${(lastReachM * 100).toFixed(0)}cm from base`}`,
    ];
  }
  if (calibrationFailureM !== undefined) {
    return [
      `CALIBRATION REJECTED  ${(calibrationFailureM * 100).toFixed(0)}cm off`,
      `the two points must be ${(ANCHOR_SEPARATION_M * 100).toFixed(0)}cm apart`,
      `(limit ${(ANCHOR_ERROR_LIMIT_M * 100).toFixed(0)}cm) — RECALIBRATE to retry`,
    ];
  }
  const next = calibration.nextAnchor;
  return next
    ? [`PLACE ${next.label}`, next.prompt, `anchor ${calibration.anchorsCaptured + 1} of 2`]
    : [];
}

// -------------------------------------------------------------- CALIBRATE --
// On a flat page there is still no AR anchor system to calibrate against, so
// this stays the honest stand-in main.ts's TEACH mode used: it records that
// the human confirmed a starting pose, not a computed T_tracking_to_struct.
// In a headset that increment is now made -- see onCalibrationPinch below,
// which runs alignment.ts's real two-anchor solve.
function calibrate(): void {
  calibrated = true;
  renderControls();
}

function recalibrate(): void {
  calibration.reset();
  calibrationFailureM = undefined;
  pinchLatch.reset();
  worldRoot.position.set(0, 0, 0);
  worldRoot.rotation.set(0, 0, 0);
  renderControls();
}

/**
 * One anchor placed, in WebXR world coordinates.
 *
 * Called on the frame a pinch closes. When the second anchor lands, the solve
 * runs and either the workspace moves to where the human put it, or the
 * measured error rejects the layout and says by how much -- the gate is the
 * number, not a judgement.
 */
function onCalibrationPinch(worldPoint: Vec3): void {
  calibration.captureAnchor(worldPoint);
  if (calibration.nextAnchor) {
    renderControls();
    return;
  }

  const placement = calibration.scenePlacement();
  if (placement) {
    worldRoot.position.set(...placement.position);
    worldRoot.rotation.set(0, placement.yaw, 0);
    calibrationFailureM = undefined;
  } else {
    // Both anchors placed, gate failed. Keep the number, drop the anchors, so
    // the next pinch starts a clean retry instead of a third anchor.
    calibrationFailureM = calibration.anchorErrorM;
    calibration.reset();
  }
  renderControls();
}

// -------------------------------------------------------------- demo loop --
function nowNs(): number {
  return Math.round(performance.now() * 1e6);
}

function onHandFrame(hand: HandFrame | null): void {
  const timestampNs = nowNs();
  shadowHand.update(hand);
  lastHandTracked = hand !== null;

  // In a headset, hand coordinates are relative to wherever the headset
  // booted; calibration is what turns them into struct_world. Undefined for
  // every other provider, whose frames are authored in struct_world already.
  const roomToStruct = handProviderKind === "openxr" ? calibration.roomToStruct : undefined;
  if (hand && handProviderKind === "openxr") updateReach(hand);

  if (!demoActive || !hand) return;

  // A calibrated transform is a precondition, not a nicety: without it the
  // recorder would bank frames measured from a meaningless origin and the
  // pipeline would happily accept them.
  if (handProviderKind === "openxr" && !roomToStruct) return;

  recorder?.captureHand(hand, timestampNs, roomToStruct);
  framesSeen = recorder?.frameCount ?? framesSeen;

  const target = wristTarget(hand);
  if (target && liveSession?.state === "open") {
    liveSession.send(hand, timestampNs, handProviderKind, roomToStruct);
  }
  renderReadout();
}

/** How far the wrist currently is from the robot's base, in struct_world.
 * The SO-101 has ~35cm of reach, so this is the number that tells a human
 * wearing a headset whether the arm can possibly follow them. */
function updateReach(hand: HandFrame): void {
  const mapped = calibration.handToStruct(hand);
  const wrist = mapped?.joints["wrist"];
  if (!wrist) {
    lastReachM = undefined;
    return;
  }
  lastReachM = Math.hypot(
    wrist.position_m[0] - ROBOT_BASE_STRUCT[0],
    wrist.position_m[1] - ROBOT_BASE_STRUCT[1],
    wrist.position_m[2] - ROBOT_BASE_STRUCT[2],
  );
}

function onShadowState(state: RobotShadowState): void {
  shadowRobot.setJoints(state.joint_positions);
  shadowRobot.setIkStatus(state.ik_status);
  lastIkStatus = state.ik_status;
  renderReadout();
}

/** Live retarget (shadow robot IK) and hand tracking are independent
 * subsystems -- one failing to start (e.g. the live-session WebSocket) must
 * never prevent the other from running. An earlier version awaited the
 * live session before starting the mock hand loop, so a single fetch
 * failure there silently stalled the whole demo (FRAMES stuck at 0,
 * caught by actually clicking through the UI, not by a unit test). */
async function startLiveSession(): Promise<void> {
  try {
    liveSession = await LiveRetargetSession.start(API_BASE, robotSelect.value);
    liveSession.connect(onShadowState);
  } catch (error) {
    console.warn("live retarget session unavailable -- shadow robot IK will not update:", error);
  }
}

/**
 * Enter the headset.
 *
 * Deliberately its own step, before CALIBRATE rather than inside START DEMO.
 * Calibration is two pinches placed in the real room, which is only possible
 * once the session is running and hands are being tracked -- starting the
 * session at START DEMO meant the human entered XR already recording, with an
 * uncalibrated workspace and no way back out to fix it.
 */
async function enterXr(): Promise<void> {
  try {
    // The DOM overlay is requested, never assumed: `started.domOverlay` says
    // whether the runtime honored it, and the in-scene HUD carries the flow
    // either way.
    const started = await startBestSession(document.body);
    await renderer.xr.setSession(started.session);
    xrSessionInfo = started;
    xrReferenceSpace = renderer.xr.getReferenceSpace() ?? undefined;

    if (wantsTransparentBackground(started.kind)) scene.background = null;
    // An 8m debug grid over a real room is noise, and in AR it paints over
    // the floor the human is standing on.
    grid.visible = false;
    orbit.enabled = false;

    recalibrate();
    hud.unplace();
  } catch (error) {
    console.warn("XR session unavailable:", error);
    readoutEl.textContent = `XR SESSION FAILED — ${String(error)}`;
  }
  renderControls();
}

async function exitXr(): Promise<void> {
  await renderer.xr.getSession()?.end();
}

/** Everything an ended session has to put back, in one place, so it runs
 * whether the human pressed EXIT XR or took the headset off. */
function onSessionEnded(): void {
  xrSessionInfo = undefined;
  xrReferenceSpace = undefined;
  scene.background = SCENE_BACKGROUND;
  grid.visible = true;
  orbit.enabled = true;
  hud.unplace();
  shadowHand.update(null);
  lastReachM = undefined;
  renderer.setAnimationLoop(flatFrame);
  renderControls();
}

async function startHandTracking(): Promise<void> {
  if (handProviderKind === "openxr") {
    // The session is entered by ENTER XR, not here; hand frames are already
    // arriving from the XR frame loop by the time a demo starts.
    if (inXr()) return;
    console.warn("HAND=openxr with no XR session — falling back to mock hand");
  } else if (handProviderKind === "webcam") {
    try {
      webcamProvider = await WebcamHandProvider.create();
      webcamPanelEl.appendChild(webcamProvider.videoElement);
      webcamPanelEl.classList.add("active");
      webcamProvider.start((hand) => onHandFrame(hand));
      return;
    } catch (error) {
      console.warn("webcam unavailable, falling back to mock hand:", error);
      webcamProvider = undefined;
      webcamPanelEl.classList.remove("active");
    }
  }
  mockProvider = await MockHandProvider.load("/spatial-training/hand/mock_episode.jsonl");
  mockProvider.start((hand) => onHandFrame(hand));
}

async function startDemo(): Promise<void> {
  recorder = new HumanEpisodeRecorder({
    taskId: "press_button", assetId: assetSelect.value, handProvider: handSelect.value as HandProviderKind,
  });
  recorder.start(nowNs());
  demoActive = true;
  framesSeen = 0;
  latestVerdict = undefined;
  renderControls();

  await Promise.all([startLiveSession(), startHandTracking()]);
}

async function finishDemo(): Promise<void> {
  demoActive = false;
  mockProvider?.stop();
  mockProvider = undefined;
  webcamProvider?.stop();
  webcamProvider = undefined;
  webcamPanelEl.classList.remove("active");
  webcamPanelEl.replaceChildren(webcamPanelLabelEl); // drop the <video>, keep the label
  liveSession?.stop();
  liveSession = undefined;
  // FINISH deliberately does NOT end the XR session any more. It used to,
  // because nothing else could -- but ejecting the human from the headset is
  // also how they missed the verdict, and how a second take meant
  // re-entering and re-calibrating from scratch. EXIT XR is the way out now,
  // and the HUD shows the verdict where they are.
  verdictPending = true;
  renderControls();

  if (!recorder) return;
  const metadata = recorder.finish();
  try {
    latestVerdict = await uploadHumanEpisode(
      API_BASE,
      metadata,
      recorder.handFrames(),
      recorder.objectStates(),
      recorder.events(),
      ASSET_WORLD_POSE,
      DEFAULT_GOAL_M,
    );
  } catch (error) {
    latestVerdict = {
      episode_id: metadata.episode_id,
      status: "rejected",
      task_success: false,
      dataset_id: null,
      rejection_reason: String(error),
    };
  }
  verdictPending = false;
  renderControls();
}

// WebXR hand frames, read once per rendered frame when a session is active
// -- separate from the mock provider's own 30Hz timer, since a real XR
// session drives its own frame callback via renderer.xr.
renderer.xr.addEventListener("sessionstart", () => {
  renderer.setAnimationLoop(xrFrame);
});

renderer.xr.addEventListener("sessionend", onSessionEnded);

/**
 * One frame inside the headset.
 *
 * Order matters. The HUD is polled before the calibration pinch so that
 * poking a button cannot also drop an anchor: a poke and a pinch are both
 * "fingers doing something", and a human reaching for START DEMO with a
 * half-closed hand would otherwise place an anchor wherever the button is.
 */
function xrFrame(_time: number, frame?: XRFrame): void {
  const hand =
    frame && xrReferenceSpace && handProviderKind === "openxr"
      ? readTrackedHand(frame, xrReferenceSpace)
      : null;

  hud.place(renderer.xr.getCamera());

  const pressed = hud.update(hand ? pokePoint(hand) : null);
  const pinched = pinchLatch.update(hand);

  if (pressed) {
    pressControl(pressed);
  } else if (hand && !calibration.isCalibrated && pinched && !demoActive) {
    const point = pinchPoint(hand);
    if (point) onCalibrationPinch(point);
  }

  if (handProviderKind === "openxr") onHandFrame(hand);
  renderer.render(scene, camera);
}

function readTrackedHand(frame: XRFrame, referenceSpace: XRReferenceSpace): HandFrame | null {
  const session = renderer.xr.getSession();
  const source = session ? preferredInputSource(session) : undefined;
  return source ? readHand(frame, source, referenceSpace) : null;
}

renderControls();
