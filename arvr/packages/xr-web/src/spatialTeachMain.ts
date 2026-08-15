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
import { preferredInputSource, readHand, wristTarget, type HandFrame } from "./hands";
import { describe as describeSession, detectCapabilities, startBestSession } from "./xr";
import { HumanEpisodeRecorder } from "./humanEpisodeRecorder";
import { uploadHumanEpisode, type SpatialEpisodeVerdict } from "./humanEpisodeUpload";
import { LiveRetargetSession, type RobotShadowState } from "./liveRetargetSession";
import { MockHandProvider } from "./mockHand";
import { ShadowHand } from "./shadowHand";
import { ShadowRobot } from "./shadowRobot";
import { WebcamHandProvider } from "./webcamHand";
import { buildEnvironment, placeAtStruct } from "./scene";
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

// Fixed struct_world placement for Milestone 1's single fixture asset/robot
// -- no scene manifest / room reconstruction in this milestone (spec
// section 40-41: fixture scene only).
//
// Recentered for the real SO-101 (Track A) -- it's a small desktop arm,
// maybe 30-40cm of usable reach, nothing like the earlier placeholder's
// ~1m chain. These aren't guessed: verified directly against the real
// IkSolver (uv run python3 against ar_datapipe.retarget.IkSolver), each
// point is the literal FK output of a real, within-joint-limits
// configuration, so IK is guaranteed to converge back to it. A 5-DOF arm
// (this one) also can't reach an arbitrary (position, orientation) pair
// independently the way a 6-DOF wrist can -- the orientation below is the
// one that FK actually produced at these positions, not an arbitrary
// identity quaternion (which was empirically NOT achievable in-limits
// near this workspace).
const ROBOT_BASE_STRUCT: Vec3 = [0.0, 0.0, 0.0];
const ASSET_WORLD_POSITION: Vec3 = [0.35, -0.05, 0.14];
// The button itself sits normally on the table -- identity orientation.
// (The wrist/gripper's *target* orientation for reaching it is a separate
// concern, handled in tools/make_mock_hand_episode.py's NATURAL_EE_ORIENTATION
// -- not the asset's own world rotation.)
const ASSET_WORLD_POSE = {
  position_m: ASSET_WORLD_POSITION,
  orientation_xyzw: [0, 0, 0, 1] as [number, number, number, number],
};
// tools/make_mock_hand_episode.py's RETRACT_END_M -- where the mock demo's
// wrist genuinely ends up, so the default goal exercises real accept logic.
const DEFAULT_GOAL_M: Vec3 = [0.37, 0.07, 0.2];

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
scene.background = new THREE.Color(0x14171c);
buildEnvironment(scene);

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

const shadowHand = new ShadowHand();
scene.add(shadowHand.root);

const shadowRobot = new ShadowRobot();
shadowRobot.placeBase(ROBOT_BASE_STRUCT);
scene.add(shadowRobot.root);

// A plain stand under the button -- Milestone 1 has no scene manifest / room
// reconstruction (fixture scene only, spec section 41), so without this the
// button asset just hangs in empty space at its world height with nothing
// visually supporting it. A visual-only prop, not a modeled InteractableAsset.
const stand = new THREE.Mesh(
  new THREE.CylinderGeometry(0.02, 0.025, ASSET_WORLD_POSITION[2], 16),
  new THREE.MeshStandardMaterial({ color: 0x4b5263, roughness: 0.8, metalness: 0.1 }),
);
placeAtStruct(stand, [ASSET_WORLD_POSITION[0], ASSET_WORLD_POSITION[1], ASSET_WORLD_POSITION[2] / 2]);
scene.add(stand);

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
    liveSession.send(hand, timestampNs, handProviderKind);
  }
  renderReadout();
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

async function startHandTracking(): Promise<void> {
  if (handProviderKind === "openxr") {
    try {
      const started = await startBestSession();
      await renderer.xr.setSession(started.session);
      xrReferenceSpace = renderer.xr.getReferenceSpace() ?? undefined;
      readoutEl.textContent = describeSession(started.kind, started.handTracking);
      renderer.xr.addEventListener("sessionend", () => {
        xrReferenceSpace = undefined;
      });
      return;
    } catch (error) {
      console.warn("XR session unavailable, falling back to mock hand:", error);
    }
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
  // Without this, FINISH on a real headset leaves the user stuck inside the
  // XR view -- nothing else in this flow ever calls session.end().
  void renderer.xr.getSession()?.end();
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
      const source = session ? preferredInputSource(session) : undefined;
      onHandFrame(source ? readHand(frame, source, xrReferenceSpace) : null);
    }
    renderer.render(scene, camera);
  });
});

renderControls();
