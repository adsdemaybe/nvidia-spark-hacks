/**
 * STRUCT browser spatial client (STRUCT_2.md 28).
 *
 * The same modes as the phone app -- PLACE, TEACH, REPLAY, FOLLOW, TWIN,
 * CORRECT -- driven by whatever spatial input is available. With a headset that
 * is a tracked controller; without one it is the mouse and WASD. Both go
 * through a SpatialAdapter, so the downstream path is identical either way,
 * which is the claim in STRUCT_2.md 5 made concrete.
 *
 * This is the optional client. It exists to prove hardware optionality and to
 * be developable without a Mac; the phone remains the primary demo device.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { VRButton } from "three/examples/jsm/webxr/VRButton.js";
import { DesktopMockAdapter, XRControllerAdapter, structToWebxr } from "./adapter";
import type { SpatialAdapter } from "./adapter";
import { ArticulatedArm, REACH_M, SHOULDER_HEIGHT, reachStatus } from "./arm";
import type { CorrectionEvent, TwinState, Vec3 } from "./contracts";
import { DEFAULT_FOLLOW_DISTANCE_M, SCHEMA_VERSION } from "./contracts";
import { gripperEvents, loadEpisode, path, poseAt, type LoadedEpisode } from "./episode";
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

const TWIN_WS = "ws://127.0.0.1:8850/twin/demo_room";
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
document.body.appendChild(VRButton.createButton(renderer));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171c);
buildEnvironment(scene);

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

const ORIGINAL_TARGET: Vec3 = [0.45, -0.05, 1.0];
let correctedTarget: Vec3 = [0.45, -0.05, 1.18];
let corrections: CorrectionEvent[] = [];

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
  console.info("CorrectionEvent captured", event);
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

  if (mode === "TEACH") {
    if (recorder.isRecording) {
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
        console.info("episode captured", captured, recorder.frames().length, "frames");
        renderControls();
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
        recorder.start(nowNs());
        renderControls();
      }, "rec");
    }
  } else if (mode === "FOLLOW") {
    add("−0.25 m", () => { followDistance = Math.max(0.25, followDistance - 0.25); });
    add("+0.25 m", () => { followDistance += 0.25; });
  } else if (mode === "TWIN") {
    add("LIVE SERVER", () => void connectTwin("live"));
    add("FIXTURE STREAM", () => void connectTwin("fixture"));
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

renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.1);
  orbit.update();
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
      if (mesh) placeAtStruct(mesh, object.position_m);
    }
  }

  readoutEl.textContent = readout();
  renderer.render(scene, camera);
});

function readout(): string {
  const p = (v: Vec3): string => v.map((n) => n.toFixed(2)).join(", ");
  const lines = [`MODE       ${mode}`, `input      ${adapter.deviceType}`];

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
    );
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
