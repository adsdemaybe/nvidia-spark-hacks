/**
 * STRUCT Sort Teleop — the Quest training demo.
 *
 * Sort red and blue balls into matching baskets with tracked hands, while the
 * real SO-101 shadow follows the same task-space intent and the whole thing
 * becomes robot-training data.
 *
 * A deliberately thin entry point. The rules live elsewhere and are tested
 * without a headset: `sortTask.ts` owns what counts as sorted, `grasp.ts`
 * owns what counts as picked up, `sortSession.ts` owns the per-frame wiring,
 * `xrCalibration.ts` owns where the workspace is in the room, and
 * `xrHud.ts` owns the controls you can reach while wearing a headset. This
 * file loads them, renders, and records.
 *
 * Why a second entry point rather than a rewrite of `spatial-teach.html`:
 * that page is the working, verified button demo, and the repo's own habit
 * (see STATE.md's "why two apps") is to leave a working thing running rather
 * than break it on the way to the next one. Both pages share every module
 * that matters -- the Quest build and the webcam build differ only at the
 * input layer, which is the property the spec actually asks for.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  PinchLatch,
  pinchPoint,
  pokePoint,
  preferredInputSource,
  readHand,
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
import {
  LiveRetargetSession,
  toWireHandFrame,
  type RobotShadowState,
  type WireHandFrame,
} from "./liveRetargetSession";
import { MockHandProvider } from "./mockHand";
import { ShadowHand } from "./shadowHand";
import { ShadowRobot } from "./shadowRobot";
import { WebcamHandProvider } from "./webcamHand";
import { buildEnvironment } from "./scene";
import { SortScene } from "./sortScene";
import { SortSession } from "./sortSession";
import {
  BALLS,
  BASKETS,
  SORT_TASK_ID,
  TABLE_Z_M,
  basketFor,
  ballsOfColor,
  type BallColor,
} from "./sortLayout";
import { ROBOT_BASE_STRUCT } from "./spatialTeachLayout";
import {
  ANCHOR_ERROR_LIMIT_M,
  XrCalibration,
  anchorSeparationM,
  type CalibrationAnchor,
} from "./xrCalibration";
import { XrHud } from "./xrHud";
import type { SortEvent } from "./sortTask";
import type { Vec3 } from "./contracts";

const API_BASE = `http://${location.hostname}:8000`;

/**
 * The two landmarks a human can actually point at in this scene.
 *
 * The button task's anchors do not exist here, so the sort scene names its
 * own: the robot's base and the red basket. They are 35cm apart, which is
 * the same order as the button pair, so the calibration gate stays as
 * meaningful (a human who places them at the wrong separation has laid out a
 * workspace the robot does not have, and gets told the measured error).
 */
const SORT_CALIBRATION_ANCHORS: readonly CalibrationAnchor[] = [
  {
    structPosition: ROBOT_BASE_STRUCT,
    label: "ROBOT BASE",
    prompt: "Pinch where the robot's base should stand",
  },
  {
    structPosition: basketFor("red").center,
    label: "RED BASKET",
    prompt: "Now pinch where the RED basket should sit",
  },
];
const SORT_ANCHOR_SEPARATION_M = anchorSeparationM(SORT_CALIBRATION_ANCHORS);

/** Where the episode's goal sits, for the existing verification contract.
 * The red basket: the sort task's own predicate is richer than a single goal
 * point (see the note in `finishDemo`), but the shared TaskSpec is a goal
 * position, so this is the honest projection of it rather than a fabricated
 * number. */
const DEFAULT_GOAL_M: Vec3 = basketFor("red").center;
const RED_BASKET_POSE = {
  position_m: basketFor("red").center,
  orientation_xyzw: [0, 0, 0, 1] as [number, number, number, number],
};

const app = document.getElementById("app")!;
const selectorsEl = document.getElementById("selectors")!;
const controlsEl = document.getElementById("controls")!;
const readoutEl = document.getElementById("readout")!;
const scoreEl = document.getElementById("score")!;
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

/** Everything in struct_world, under the transform calibration solves. */
const worldRoot = new THREE.Group();
scene.add(worldRoot);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.01, 100);
camera.position.set(0.55, 0.55, 0.7);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0.26, TABLE_Z_M, 0);
orbit.enableDamping = true;
orbit.update();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const sortScene = new SortScene();
worldRoot.add(sortScene.root);

const shadowRobot = new ShadowRobot();
shadowRobot.placeBase(ROBOT_BASE_STRUCT);
worldRoot.add(shadowRobot.root);

// Outside worldRoot: the shadow hand is drawn on the human's real hand, in
// the room, not on the workspace.
const shadowHand = new ShadowHand();
scene.add(shadowHand.root);

const hud = new XrHud();
scene.add(hud.root);

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
let lastIkStatus: RobotShadowState["ik_status"] | undefined;
let latestVerdict: SpatialEpisodeVerdict | undefined;
let verdictPending = false;
let lastFrameTimeMs: number | undefined;

const session = new SortSession();
const calibration = new XrCalibration(SORT_CALIBRATION_ANCHORS);
const pinchLatch = new PinchLatch();
let xrReferenceSpace: XRReferenceSpace | undefined;
let xrSessionInfo: StartedSession | undefined;
let calibrationFailureM: number | undefined;

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
const handSelect = addSelector("Hand", "hand-select", ["mock"]);
addSelector("Simulator", "sim-select", ["mujoco"]);
handSelect.addEventListener("change", () => {
  handProviderKind = handSelect.value as HandProviderKind;
  renderControls();
});

void detectCapabilities().then((caps) => {
  if (caps.best !== "flat") {
    const opt = document.createElement("option");
    opt.value = "openxr";
    opt.textContent = "OPENXR";
    handSelect.appendChild(opt);
  }
});

if (navigator.mediaDevices) {
  const opt = document.createElement("option");
  opt.value = "webcam";
  opt.textContent = "WEBCAM";
  handSelect.appendChild(opt);
}

function inXr(): boolean {
  return xrSessionInfo !== undefined;
}

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

/** The flow, described once so the DOM bar and the in-headset HUD cannot
 * drift into offering different actions. */
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

  if (!demoActive) specs.push({ id: "reset", label: "RESET BALLS", action: resetBalls });

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
    const button = document.createElement("button");
    button.textContent = spec.label;
    if (spec.className) button.className = spec.className;
    button.onclick = spec.action;
    button.disabled = spec.disabled ?? false;
    controlsEl.appendChild(button);
  }
  hud.setButtons(specs.filter((s) => !s.disabled).map((s) => ({ id: s.id, label: s.label })));
  renderReadout();
}

function pressControl(id: string): void {
  controlSpecs().find((spec) => spec.id === id && !spec.disabled)?.action();
}

function renderScore(): void {
  scoreEl.replaceChildren();
  for (const color of ["red", "blue"] as BallColor[]) {
    const span = document.createElement("span");
    const total = ballsOfColor(color).length;
    span.className = color;
    span.textContent = `${color.toUpperCase()} ${session.task.score(color)}/${total}`;
    scoreEl.appendChild(span);
  }
  if (session.task.isComplete) {
    const done = document.createElement("span");
    done.className = "done";
    done.textContent = "SORTED ✓";
    scoreEl.appendChild(done);
  }
}

function renderReadout(): void {
  calibrationEl.dataset["calibrated"] = String(isCalibrated());
  calibrationEl.textContent = isCalibrated() ? "CALIBRATED" : "NOT CALIBRATED";
  renderScore();

  const redTotal = ballsOfColor("red").length;
  const blueTotal = ballsOfColor("blue").length;
  const lines: string[] = [
    `task       ${SORT_TASK_ID}`,
    `robot      ${robotSelect.value}`,
    `hand       ${handSelect.value}`,
    "",
    `RED   ${session.task.score("red")}/${redTotal}`,
    `BLUE  ${session.task.score("blue")}/${blueTotal}`,
  ];

  const held = session.task ? heldBallId : null;
  if (held) {
    const ball = BALLS.find((b) => b.id === held)!;
    lines.push("", `CURRENT   ${held}`, `TARGET    ${basketFor(ball.color).id}`);
  }
  lines.push(
    "",
    `HAND      ${lastHandTracked ? "TRACKED" : "LOST"}`,
    `PINCH     ${pinchActive ? "CLOSED" : "OPEN"}`,
    `IK        ${lastIkStatus ?? "…"}`,
  );

  if (inXr() && xrSessionInfo) {
    lines.push(
      "",
      describeSession(xrSessionInfo.kind, xrSessionInfo.handTracking),
      `dom overlay  ${xrSessionInfo.domOverlay ? "granted" : "not granted"}`,
    );
    if (!xrSessionInfo.handTracking) {
      lines.push("NO HAND TRACKING — enable it in the headset's settings");
    }
    lines.push(...calibrationLines());
  }

  if (demoActive) lines.push("", "● RECORDING", `FRAMES    ${framesSeen}`);
  else if (verdictPending) lines.push("", "verifying…");
  else if (latestVerdict) {
    const ok = latestVerdict.status === "accepted";
    lines.push(
      "",
      `RETARGET/VERIFY  ${ok ? "PASS" : "FAIL"}`,
      ok ? "DEMONSTRATION ACCEPTED" : `REJECTED: ${latestVerdict.rejection_reason ?? "unknown"}`,
    );
    if (latestVerdict.dataset_id) lines.push(`dataset  ${latestVerdict.dataset_id}`);
  }

  if (session.task.isComplete) lines.push("", "SORTED ✓");

  const text = lines.filter((line) => line !== "").join("\n");
  readoutEl.textContent = text;
  hud.setText(text);
}

function calibrationLines(): string[] {
  if (calibration.isCalibrated) {
    return [`CALIBRATED  anchor error ${((calibration.anchorErrorM ?? 0) * 100).toFixed(1)}cm`];
  }
  if (calibrationFailureM !== undefined) {
    return [
      `CALIBRATION REJECTED  ${(calibrationFailureM * 100).toFixed(0)}cm off`,
      `the two points must be ${(SORT_ANCHOR_SEPARATION_M * 100).toFixed(0)}cm apart`,
      `(limit ${(ANCHOR_ERROR_LIMIT_M * 100).toFixed(0)}cm) — RECALIBRATE to retry`,
    ];
  }
  const next = calibration.nextAnchor;
  return next
    ? [`PLACE ${next.label}`, next.prompt, `anchor ${calibration.anchorsCaptured + 1} of 2`]
    : [];
}

// -------------------------------------------------------------- CALIBRATE --
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
    calibrationFailureM = calibration.anchorErrorM;
    calibration.reset();
  }
  renderControls();
}

function resetBalls(): void {
  session.reset();
  sortScene.update(session.task, null);
  renderControls();
}

// -------------------------------------------------------------- demo loop --
function nowNs(): number {
  return Math.round(performance.now() * 1e6);
}

let heldBallId: string | null = null;
let pinchActive = false;

/**
 * One tracked hand frame, from whichever provider is running.
 *
 * The frame arrives in WebXR space. Converting it to struct_world here, once,
 * is what lets everything downstream -- the grasp test, the task predicate,
 * the recorder and the retargeter -- work in one frame of reference. For the
 * headset that conversion includes the workspace calibration; the mock and
 * webcam providers already author struct_world, so they pass no alignment.
 */
function onHandFrame(hand: HandFrame | null): void {
  const timestampNs = nowNs();
  shadowHand.update(hand);
  lastHandTracked = hand !== null;

  const roomToStruct = handProviderKind === "openxr" ? calibration.roomToStruct : undefined;
  // Without a calibration there is no honest struct_world answer for a
  // headset frame, so the interaction simply does not run -- rather than
  // running against coordinates measured from wherever the headset booted.
  if (handProviderKind === "openxr" && !roomToStruct) {
    renderReadout();
    return;
  }

  const frame: WireHandFrame | null = hand
    ? toWireHandFrame(hand, timestampNs, handProviderKind, roomToStruct)
    : null;

  const now = performance.now();
  const dtSeconds = lastFrameTimeMs === undefined ? 1 / 72 : (now - lastFrameTimeMs) / 1000;
  lastFrameTimeMs = now;

  const update = session.update({
    frame,
    gripper: hand?.gripper ?? 0,
    // Clamped: a tab that was backgrounded returns a multi-second delta,
    // which would teleport every falling ball through the table.
    dtSeconds: Math.min(dtSeconds, 0.05),
  });

  heldBallId = update.heldId;
  pinchActive = update.pinchActive;
  sortScene.update(session.task, update.highlightId);

  if (demoActive) recordFrame(hand, frame, timestampNs, update.newEvents);
  renderReadout();
}

/** Everything this frame contributes to the episode. */
function recordFrame(
  hand: HandFrame | null,
  frame: WireHandFrame | null,
  timestampNs: number,
  newEvents: SortEvent[],
): void {
  if (!recorder) return;

  if (hand) {
    const roomToStruct = handProviderKind === "openxr" ? calibration.roomToStruct : undefined;
    recorder.captureHand(hand, timestampNs, roomToStruct);
    framesSeen = recorder.frameCount;
  }

  // Ball poses as a time series -- a policy cannot be learned from object
  // motion that cannot be lined up with the hand frames that caused it.
  for (const ball of BALLS) {
    recorder.captureObject({
      id: ball.id,
      position_m: session.task.ballPosition(ball.id),
      timestamp_ns: timestampNs,
    });
  }

  for (const event of newEvents) {
    recorder.markEvent(event.type, timestampNs, {
      ...(event.objectId ? { objectId: event.objectId } : {}),
      ...(event.containerId ? { containerId: event.containerId } : {}),
    });
  }

  if (frame && liveSession?.state === "open") {
    const roomToStruct = handProviderKind === "openxr" ? calibration.roomToStruct : undefined;
    if (hand) liveSession.send(hand, timestampNs, handProviderKind, roomToStruct);
  }
}

function onShadowState(state: RobotShadowState): void {
  shadowRobot.setJoints(state.joint_positions);
  shadowRobot.setIkStatus(state.ik_status);
  lastIkStatus = state.ik_status;
}

async function startLiveSession(): Promise<void> {
  try {
    liveSession = await LiveRetargetSession.start(API_BASE, robotSelect.value);
    liveSession.connect(onShadowState);
  } catch (error) {
    // Spec section 26: the human keeps sorting when the backend is gone.
    // The robot shadow freezes and says so; nothing is invented for it.
    console.warn("live retarget session unavailable — robot shadow will not move:", error);
    lastIkStatus = undefined;
  }
}

async function enterXr(): Promise<void> {
  try {
    const started = await startBestSession(document.body);
    await renderer.xr.setSession(started.session);
    xrSessionInfo = started;
    xrReferenceSpace = renderer.xr.getReferenceSpace() ?? undefined;
    if (wantsTransparentBackground(started.kind)) scene.background = null;
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

function onSessionEnded(): void {
  xrSessionInfo = undefined;
  xrReferenceSpace = undefined;
  scene.background = SCENE_BACKGROUND;
  grid.visible = true;
  orbit.enabled = true;
  hud.unplace();
  shadowHand.update(null);
  renderer.setAnimationLoop(flatFrame);
  renderControls();
}

async function startHandTracking(): Promise<void> {
  if (handProviderKind === "openxr") {
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
    taskId: SORT_TASK_ID,
    assetId: BALLS[0]!.id,
    handProvider: handSelect.value as HandProviderKind,
  });
  recorder.start(nowNs());
  recorder.markEvent("task_start", nowNs());

  // Basket poses are static, so they are recorded once rather than per
  // frame -- but they ARE recorded, because a demonstration is only
  // reusable if the thing the ball went into is part of the record.
  for (const basket of BASKETS) {
    recorder.captureObject({ id: basket.id, position_m: [...basket.center] as Vec3 });
  }

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
  webcamPanelEl.replaceChildren(webcamPanelLabelEl);
  liveSession?.stop();
  liveSession = undefined;
  verdictPending = true;

  if (recorder) recorder.markEvent("task_finish", nowNs());
  renderControls();

  if (!recorder) return;
  const metadata = recorder.finish();
  try {
    // The shared verification contract's TaskSpec is a goal point, so the
    // upload carries the red basket as the goal. That is a projection of
    // this task's real predicate (every ball in its matching basket), not
    // the whole of it -- the full predicate lives in the event stream, which
    // is why ball_enter_basket / wrong_basket / sort_complete are recorded
    // with the objects they concern. Teaching the backend the richer
    // predicate is the next increment, not something to fake here.
    latestVerdict = await uploadHumanEpisode(
      API_BASE,
      metadata,
      recorder.handFrames(),
      recorder.objectStates(),
      recorder.events(),
      RED_BASKET_POSE,
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

// ------------------------------------------------------------ frame loops --
function flatFrame(): void {
  orbit.update();
  renderer.render(scene, camera);
}
renderer.setAnimationLoop(flatFrame);

renderer.xr.addEventListener("sessionstart", () => {
  renderer.setAnimationLoop(xrFrame);
});
renderer.xr.addEventListener("sessionend", onSessionEnded);

/**
 * One frame inside the headset.
 *
 * The HUD is polled before the calibration pinch, so reaching for a button
 * with a half-closed hand cannot also drop an anchor where the button is.
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
  const session_ = renderer.xr.getSession();
  const source = session_ ? preferredInputSource(session_) : undefined;
  return source ? readHand(frame, source, referenceSpace) : null;
}

sortScene.update(session.task, null);
renderControls();
