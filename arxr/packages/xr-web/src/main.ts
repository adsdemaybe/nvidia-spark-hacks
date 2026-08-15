/**
 * STRUCT browser spatial client (STRUCT_2.md 28).
 *
 * The same modes as the phone app -- PLACE, TEACH, FOLLOW, TWIN, CORRECT --
 * driven by whatever spatial input is available. With a headset that is a
 * tracked controller; without one it is the mouse and WASD. Both go through a
 * SpatialAdapter, so the downstream path is identical either way, which is the
 * claim in STRUCT_2.md 5 made concrete.
 *
 * This is the optional client. It exists to prove hardware optionality and to
 * be developable without a Mac; the phone remains the primary demo device.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { VRButton } from "three/examples/jsm/webxr/VRButton.js";
import { DesktopMockAdapter, XRControllerAdapter, structToWebxr } from "./adapter";
import type { SpatialAdapter } from "./adapter";
import { DEFAULT_FOLLOW_DISTANCE_M, type TwinState, type Vec3 } from "./contracts";
import { EpisodeRecorder } from "./recorder";
import { Trail, buildEnvironment, loadScene, makeTargetLine, placeAtStruct, updateLine } from "./scene";
import { followTarget } from "./spatial";
import {
  MockTwinStateProvider,
  WebSocketTwinStateProvider,
  type TwinStateProvider,
} from "./twinProvider";

const MODES = ["PLACE", "TEACH", "REPLAY", "FOLLOW", "TWIN", "CORRECT"] as const;
type Mode = (typeof MODES)[number];

const TWIN_WS = "ws://127.0.0.1:8850/twin/demo_room";
const FIXTURE_TWIN = "/ar-xr/fake_twin_state.jsonl";

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
document.body.appendChild(VRButton.createButton(renderer));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171c);
buildEnvironment(scene);

// Framed to hold the whole workspace -- robot, table, cube and bin -- plus the
// couple of metres of floor the human walks in FOLLOW.
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

// ------------------------------------------------------------------ markers --
const human = marker(0x00d4aa, 0.09);
const followMarker = marker(0xffb347, 0.07);
const endEffector = marker(0xe06c75, 0.045);
const correctionGhost = marker(0xc678dd, 0.055);
scene.add(human, followMarker, endEffector, correctionGhost);

const followLine = makeTargetLine();
scene.add(followLine);

const trail = new Trail();
scene.add(trail.line);

function marker(color: number, radius: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 20, 16),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.35 }),
  );
  mesh.castShadow = true;
  return mesh;
}

// --------------------------------------------------------------------- state --
let mode: Mode = "TWIN";
let humanPosition: Vec3 = [1.6, 0.0, 0.0];
let humanYaw = Math.PI;
let followDistance = DEFAULT_FOLLOW_DISTANCE_M;
let latestTwin: TwinState | undefined;
let provider: TwinStateProvider | undefined;
let robotJointPhase = 0;

const adapter: SpatialAdapter = navigator.xr ? new XRControllerAdapter() : new DesktopMockAdapter();
const recorder = new EpisodeRecorder({
  taskId: "cube_to_bin",
  deviceType: adapter.deviceType,
  inputType: adapter.inputType,
});

const keys = new Set<string>();
addEventListener("keydown", (e) => keys.add(e.key.toLowerCase()));
addEventListener("keyup", (e) => keys.delete(e.key.toLowerCase()));

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

  if (mode === "TEACH") {
    if (recorder.isRecording) {
      add("GRAB", () => recorder.grab(nowNs()));
      add("RELEASE", () => recorder.release(nowNs()));
      add("FINISH", () => {
        const episode = recorder.finish(nowNs());
        console.info("episode captured", episode, recorder.frames().length, "frames");
        renderControls();
      }, "rec");
      add("CANCEL", () => {
        recorder.cancel(nowNs());
        trail.clear();
        renderControls();
      });
    } else {
      add("START DEMO", () => {
        trail.clear();
        recorder.start(nowNs());
        renderControls();
      }, "rec");
    }
  } else if (mode === "FOLLOW") {
    add("−0.25 m", () => { followDistance = Math.max(0.25, followDistance - 0.25); });
    add("+0.25 m", () => { followDistance += 0.25; });
  } else if (mode === "TWIN") {
    add("USE LIVE ISAAC/MOCK SERVER", () => connectTwin("live"));
    add("USE FIXTURE STREAM", () => connectTwin("fixture"));
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

// -------------------------------------------------------------------- loop --
function nowNs(): number {
  return Math.round(performance.now() * 1e6);
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

const clock = new THREE.Clock();

renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.1);
  orbit.update();
  stepHuman(dt);

  const humanQuat: [number, number, number, number] = [
    0, 0, Math.sin(humanYaw / 2), Math.cos(humanYaw / 2),
  ];
  const humanPose = { position_m: humanPosition, orientation_xyzw: humanQuat };
  placeAtStruct(human, humanPosition);

  const target = followTarget(humanPose, followDistance);
  const showFollow = mode === "FOLLOW";
  followMarker.visible = showFollow;
  followLine.visible = showFollow;
  if (showFollow) {
    placeAtStruct(followMarker, target);
    updateLine(followLine, humanPosition, target);
  }

  // The end effector rides the human's hand position in the desktop fallback.
  const effector: Vec3 = [humanPosition[0], humanPosition[1], 0.95];
  placeAtStruct(endEffector, effector);
  endEffector.visible = mode === "TEACH" || mode === "REPLAY";
  correctionGhost.visible = mode === "CORRECT";
  if (mode === "CORRECT") placeAtStruct(correctionGhost, [0.45, -0.05, 1.18]);

  if (mode === "TEACH" && recorder.isRecording) {
    const frame = adapter.toSpatialFrame(
      { position: structToWebxr.position(effector), orientation: [0, 0, 0, 1], trigger: 0 },
      nowNs(),
    );
    recorder.capture(frame);
    trail.push(effector);
  }
  trail.line.visible = mode === "TEACH" || mode === "REPLAY";

  if (latestTwin) {
    robotJointPhase = latestTwin.robot.joint_positions[0] ?? 0;
    for (const object of latestTwin.objects) {
      const mesh = sceneObjects?.get(object.id.replace(/_\d+$/, ""));
      if (mesh) placeAtStruct(mesh, object.position_m);
    }
  }

  readoutEl.textContent = readout();
  renderer.render(scene, camera);
});

function readout(): string {
  const p = (v: Vec3): string => v.map((n) => n.toFixed(2)).join(", ");
  const lines = [`MODE       ${mode}`, `input      ${adapter.deviceType}`];

  if (mode === "FOLLOW") {
    const target = followTarget(
      { position_m: humanPosition, orientation_xyzw: [0, 0, Math.sin(humanYaw / 2), Math.cos(humanYaw / 2)] },
      followDistance,
    );
    const actual = Math.hypot(
      humanPosition[0] - target[0], humanPosition[1] - target[1], humanPosition[2] - target[2],
    );
    lines.push(`human      ${p(humanPosition)}`, `target     ${p(target)}`,
      `desired    ${followDistance.toFixed(2)} m`, `actual     ${actual.toFixed(2)} m`);
  }
  if (mode === "TEACH") {
    lines.push(
      recorder.isRecording ? "RECORDING  ●" : "idle",
      `frames     ${recorder.frameCount}`,
      `duration   ${recorder.durationSeconds.toFixed(1)} s`,
    );
  }
  if (latestTwin) {
    lines.push(
      `scene      ${latestTwin.scene_id}`,
      `task       ${latestTwin.task?.id ?? "-"} ${latestTwin.task?.status ?? ""}`,
      `joint[0]   ${robotJointPhase.toFixed(3)}`,
    );
  }
  return lines.join("\n");
}

// ------------------------------------------------------------------- boot --
let sceneObjects: Map<string, THREE.Object3D> | undefined;

loadScene()
  .then((loaded) => {
    sceneObjects = loaded.objects;
    scene.add(loaded.root);
    readoutEl.textContent = "scene loaded";
    return connectTwin("fixture");
  })
  .catch((error: unknown) => {
    readoutEl.textContent = `scene failed to load:\n${String(error)}`;
    provenanceEl.textContent = "SCENE UNAVAILABLE";
  });

renderControls();
