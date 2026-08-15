/**
 * STRUCT Spatial Teach — Shadow Robot Spatial Demonstration Pipeline spec
 * section 43 (Minimal UI), the new entry point for the pivot away from the
 * mode-based app (main.ts, untouched). ROBOT/ASSET/HAND/SIMULATOR selectors,
 * CALIBRATE, START DEMO, FINISH -- a single flow, not a mode switcher.
 *
 * Tested and verified end-to-end this session with HAND = mock
 * (MockHandProvider). HAND = openxr wires the same real WebXR hand-tracking
 * path xr.ts/hands.ts already use elsewhere in this client, but hasn't been
 * live-tested against real hardware in this session (no headset attached) --
 * flagged honestly in STATE.md rather than silently assumed working.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { readHand, wristTarget, type HandFrame } from "./hands";
import { describe as describeSession, detectCapabilities, startBestSession } from "./xr";
import { HumanEpisodeRecorder } from "./humanEpisodeRecorder";
import { uploadHumanEpisode, type SpatialEpisodeVerdict } from "./humanEpisodeUpload";
import { LiveRetargetSession, type RobotShadowState } from "./liveRetargetSession";
import { MockHandProvider } from "./mockHand";
import { ShadowHand } from "./shadowHand";
import { ShadowRobot } from "./shadowRobot";
import { buildEnvironment, placeAtStruct } from "./scene";
import type { Vec3 } from "./contracts";

const API_BASE = "http://127.0.0.1:8000";

// Fixed struct_world placement for Milestone 1's single fixture asset/robot
// -- no scene manifest / room reconstruction in this milestone (spec
// section 40-41: fixture scene only).
const ROBOT_BASE_STRUCT: Vec3 = [0.15, -0.7, 0.0];
const ASSET_WORLD_POSITION: Vec3 = [0.4, 0.0, 0.53];
const ASSET_WORLD_POSE = {
  position_m: ASSET_WORLD_POSITION,
  orientation_xyzw: [0, 0, 0, 1] as [number, number, number, number],
};
// tools/make_mock_hand_episode.py's RETRACT_END_M -- where the mock demo's
// wrist genuinely ends up, so the default goal exercises real accept logic.
const DEFAULT_GOAL_M: Vec3 = [0.2, -0.15, 0.7];

const app = document.getElementById("app")!;
const selectorsEl = document.getElementById("selectors")!;
const controlsEl = document.getElementById("controls")!;
const readoutEl = document.getElementById("readout")!;
const calibrationEl = document.getElementById("calibration")!;

// ---------------------------------------------------------------- renderer --
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.xr.enabled = true;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171c);
buildEnvironment(scene);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.01, 100);
camera.position.set(1.6, 1.4, 2.0);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0.3, 0.4, 0.0);
orbit.enableDamping = true;
orbit.update();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const shadowHand = new ShadowHand();
scene.add(shadowHand.root);

const shadowRobot = new ShadowRobot();
shadowRobot.placeBase(ROBOT_BASE_STRUCT);
scene.add(shadowRobot.root);

void new GLTFLoader().loadAsync("/spatial-training/assets/button/asset.glb").then((gltf) => {
  gltf.scene.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      (child as THREE.Mesh).material = new THREE.MeshStandardMaterial({
        color: 0xe5c07b, roughness: 0.6, metalness: 0.1,
      });
    }
  });
  placeAtStruct(gltf.scene, ASSET_WORLD_POSITION);
  scene.add(gltf.scene);
});

renderer.setAnimationLoop(() => {
  orbit.update();
  renderer.render(scene, camera);
});

// ------------------------------------------------------------------- state --
type HandProviderKind = "mock" | "openxr";
let handProviderKind: HandProviderKind = "mock";
let mockProvider: MockHandProvider | undefined;
let calibrated = false;
let recorder: HumanEpisodeRecorder | undefined;
let liveSession: LiveRetargetSession | undefined;
let demoActive = false;
let framesSeen = 0;
let lastHandTracked = false;
let lastIkStatus: RobotShadowState["ik_status"] | undefined;
let latestVerdict: SpatialEpisodeVerdict | undefined;
let verdictPending = false;

// ----------------------------------------------------------------- WebXR --
let xrReferenceSpace: XRReferenceSpace | undefined;
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
});

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

function renderControls(): void {
  controlsEl.replaceChildren();
  if (!calibrated) {
    addButton("CALIBRATE", calibrate);
  } else if (!demoActive) {
    addButton("START DEMO", startDemo);
  } else {
    addButton("FINISH", finishDemo, "rec");
  }
  if (latestVerdict?.status === "accepted") {
    addButton("ADD TO TRAINING SET ✓", () => {}, "accepted").disabled = true;
  }
  renderReadout();
}

function renderReadout(): void {
  calibrationEl.dataset["calibrated"] = String(calibrated);
  calibrationEl.textContent = calibrated ? "CALIBRATED" : "NOT CALIBRATED";

  const lines: string[] = [
    `robot      ${robotSelect.value}`,
    `asset      ${assetSelect.value}`,
    `hand       ${handSelect.value}`,
  ];
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
  readoutEl.textContent = lines.filter((l) => l !== "").join("\n");
}

// -------------------------------------------------------------- CALIBRATE --
// spec section 18: "there's no real AR anchor system yet to calibrate
// against" here either -- same honest stand-in as main.ts's TEACH mode:
// this records that the human explicitly confirmed a starting pose before
// recording is allowed to start, not a claim of real T_tracking_to_struct
// computation. Wiring alignment.ts's real two-anchor solve into this entry
// point is the natural next increment.
function calibrate(): void {
  calibrated = true;
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

  if (!demoActive || !hand) return;

  recorder?.captureHand(hand, timestampNs);
  framesSeen = recorder?.frameCount ?? framesSeen;

  const target = wristTarget(hand);
  if (target && liveSession?.state === "open") {
    liveSession.send(hand, timestampNs);
  }
  renderReadout();
}

function onShadowState(state: RobotShadowState): void {
  shadowRobot.setJoints(state.joint_positions);
  shadowRobot.setIkStatus(state.ik_status);
  lastIkStatus = state.ik_status;
  renderReadout();
}

async function startDemo(): Promise<void> {
  recorder = new HumanEpisodeRecorder({
    taskId: "press_button", assetId: assetSelect.value, handProvider: handSelect.value as "mock" | "openxr",
  });
  recorder.start(nowNs());
  demoActive = true;
  framesSeen = 0;
  latestVerdict = undefined;
  renderControls();

  liveSession = await LiveRetargetSession.start(API_BASE, robotSelect.value);
  liveSession.connect(onShadowState);

  if (handProviderKind === "mock") {
    mockProvider = await MockHandProvider.load("/spatial-training/hand/mock_episode.jsonl");
    mockProvider.start((hand) => onHandFrame(hand));
  } else {
    try {
      const started = await startBestSession();
      await renderer.xr.setSession(started.session);
      xrReferenceSpace = renderer.xr.getReferenceSpace() ?? undefined;
      readoutEl.textContent = describeSession(started.kind, started.handTracking);
      renderer.xr.addEventListener("sessionend", () => {
        xrReferenceSpace = undefined;
      });
    } catch (error) {
      console.warn("XR session unavailable, falling back to mock hand:", error);
      mockProvider = await MockHandProvider.load("/spatial-training/hand/mock_episode.jsonl");
      mockProvider.start((hand) => onHandFrame(hand));
    }
  }
}

async function finishDemo(): Promise<void> {
  demoActive = false;
  mockProvider?.stop();
  mockProvider = undefined;
  liveSession?.stop();
  liveSession = undefined;
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
  renderer.setAnimationLoop((_time, frame) => {
    orbit.update();
    if (frame && xrReferenceSpace && handProviderKind === "openxr") {
      const session = renderer.xr.getSession();
      for (const source of session?.inputSources ?? []) {
        if (source.hand) {
          const hand = readHand(frame, source, xrReferenceSpace);
          onHandFrame(hand);
        }
      }
    }
    renderer.render(scene, camera);
  });
});

renderControls();
