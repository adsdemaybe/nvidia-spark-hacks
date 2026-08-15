/**
 * STRUCT Ball Pit — the room-scale immersive demo.
 *
 * Stand in a room, grab 18cm balls off the floor with either hand, and drop
 * or throw them into the matching bin. No robot: nothing at this scale is
 * reachable by a 35cm desk arm, so rather than render a shadow robot that
 * silently cannot follow, this scene omits it. The desk-scale
 * `sort-teleop.html` remains the robot-teleop demo.
 *
 * The launcher is deliberately one enormous button. The first version of the
 * headset flow hid "enter XR" behind first changing a dropdown, and the
 * result was people looking at a flat browser tab wondering why nothing was
 * immersive. A WebXR session is a full environment -- the browser disappears
 * entirely -- but only if the button to start it is impossible to miss.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { readBothHands, type HandFrame, type HandPair } from "./hands";
import {
  describe as describeSession,
  detectCapabilities,
  startBestSession,
  type StartedSession,
} from "./xr";
import { ShadowHands } from "./shadowHand";
import { BallPitScene, buildBallPitLighting } from "./ballPitScene";
import { BallPitSession, type HandInput } from "./ballPitSession";
import { BallPitTask } from "./ballPitTask";
import {
  BALLS_PER_COLOR,
  BALL_PIT_TASK_ID,
  binFor,
  type BallColor,
} from "./ballPitLayout";
import { webxrToStruct } from "./adapter";
import { XrHud } from "./xrHud";
import type { Vec3 } from "./contracts";

const app = document.getElementById("app")!;
const launcherEl = document.getElementById("launcher")!;
const enterButton = document.getElementById("enter-xr") as HTMLButtonElement;
const statusEl = document.getElementById("xr-status")!;
const secondaryEl = document.getElementById("secondary")!;
const readoutEl = document.getElementById("readout")!;
const scoreEl = document.getElementById("score")!;
const controlsEl = document.getElementById("controls")!;

// ---------------------------------------------------------------- renderer --
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.xr.enabled = true;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1116);
// A little fog so the 30m floor fades toward the horizon instead of ending in
// a hard edge, which in a headset reads as the world running out.
scene.fog = new THREE.Fog(0x0e1116, 6, 22);
buildBallPitLighting(scene);

const pitScene = new BallPitScene();
scene.add(pitScene.root);

const shadowHands = new ShadowHands();
scene.add(shadowHands.root);

const hud = new XrHud();
scene.add(hud.root);

// Flat preview camera: standing where the XR session will put you (struct
// origin, eye height) looking out at the pit, so the launcher previews the
// actual view you are about to step into rather than an arbitrary angle.
// These are three.js coordinates, so they are the struct positions pushed
// through structToWebxr: struct (0,0,1.6) -> three (0,1.6,0), and looking
// toward struct +X is looking toward three -Z.
const camera = new THREE.PerspectiveCamera(65, innerWidth / innerHeight, 0.05, 100);
camera.position.set(0, 1.6, 0);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0.35, -1.3);
orbit.enableDamping = true;
orbit.update();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// ------------------------------------------------------------------- state --
const task = new BallPitTask();
const session = new BallPitSession(task);
let xrSessionInfo: StartedSession | undefined;
let xrReferenceSpace: XRReferenceSpace | undefined;
let lastFrameMs: number | undefined;
let lastHands: HandPair = { left: null, right: null };

// ---------------------------------------------------------------- launcher --
void detectCapabilities().then((caps) => {
  if (caps.vr || caps.ar) {
    enterButton.disabled = false;
    enterButton.textContent = "ENTER VR";
    statusEl.textContent = caps.vr
      ? "Immersive VR available. Put the headset on and press the button."
      : "Only passthrough AR is available on this device; entering that instead.";
    return;
  }

  // Say plainly why, rather than leaving a dead button. Every one of these is
  // a different fix, and "it didn't work" is not a diagnosis.
  enterButton.textContent = "NO IMMERSIVE SESSION";
  statusEl.classList.add("bad");
  statusEl.textContent = caps.webxr
    ? [
        "This browser has WebXR but offers no immersive session.",
        "Usually that means this is a desktop browser, or the page is not on",
        "a secure origin (https, or localhost).",
        ...caps.notes,
      ].join("\n")
    : [
        "navigator.xr is missing -- no WebXR in this browser at all.",
        "Open this page in the headset's own browser, over https.",
      ].join("\n");
});

enterButton.onclick = () => void enterXr();

addSecondary("FLAT PREVIEW", () => {
  launcherEl.classList.add("hidden");
});

function addSecondary(label: string, onClick: () => void): void {
  const button = document.createElement("button");
  button.textContent = label;
  button.onclick = onClick;
  secondaryEl.appendChild(button);
}

async function enterXr(): Promise<void> {
  try {
    // VR rather than AR: this scene brings its own floor and horizon, and
    // passthrough would show the real room through a floor meant to be under
    // your feet. See startBestSession's `prefer` argument.
    const started = await startBestSession(document.body, "vr");
    await renderer.xr.setSession(started.session);
    xrSessionInfo = started;
    xrReferenceSpace = renderer.xr.getReferenceSpace() ?? undefined;
    launcherEl.classList.add("hidden");
    hud.unplace();
  } catch (error) {
    statusEl.classList.add("bad");
    statusEl.textContent = `Could not start an immersive session:\n${String(error)}`;
  }
}

// -------------------------------------------------------------------- HUD --
function renderScore(): void {
  scoreEl.replaceChildren();
  for (const color of ["red", "blue"] as BallColor[]) {
    const span = document.createElement("span");
    span.className = color;
    span.textContent = `${color.toUpperCase()} ${task.score(color)}/${BALLS_PER_COLOR}`;
    scoreEl.appendChild(span);
  }
  if (task.isComplete) {
    const done = document.createElement("span");
    done.className = "done";
    done.textContent = "CLEARED ✓";
    scoreEl.appendChild(done);
  }

  const lines = [
    `task    ${BALL_PIT_TASK_ID}`,
    `RED     ${task.score("red")}/${BALLS_PER_COLOR}   -> ${binFor("red").id}`,
    `BLUE    ${task.score("blue")}/${BALLS_PER_COLOR}   -> ${binFor("blue").id}`,
    `hands   L:${lastHands.left ? "tracked" : "--"}  R:${lastHands.right ? "tracked" : "--"}`,
  ];
  if (xrSessionInfo) {
    lines.push(describeSession(xrSessionInfo.kind, xrSessionInfo.handTracking));
    if (!xrSessionInfo.handTracking) {
      lines.push("NO HAND TRACKING — enable it in the headset settings");
    }
  }
  if (task.isComplete) lines.push("", "CLEARED ✓");

  const text = lines.join("\n");
  readoutEl.textContent = text;
  hud.setText(text);
}

function resetPit(): void {
  task.reset();
  session.reset();
  renderScore();
}

const resetButton = document.createElement("button");
resetButton.textContent = "RESET BALLS";
resetButton.onclick = resetPit;
controlsEl.appendChild(resetButton);
hud.setButtons([{ id: "reset", label: "RESET" }]);

// ------------------------------------------------------------ frame loops --
function flatFrame(): void {
  orbit.update();
  stepPhysicsOnly();
  renderer.render(scene, camera);
}
renderer.setAnimationLoop(flatFrame);

renderer.xr.addEventListener("sessionstart", () => renderer.setAnimationLoop(xrFrame));
renderer.xr.addEventListener("sessionend", () => {
  xrSessionInfo = undefined;
  xrReferenceSpace = undefined;
  lastHands = { left: null, right: null };
  shadowHands.update({ left: null, right: null });
  hud.unplace();
  launcherEl.classList.remove("hidden");
  renderer.setAnimationLoop(flatFrame);
});

/** Balls still fall and settle on the flat page, so the preview is alive
 * rather than a frozen still. */
function stepPhysicsOnly(): void {
  task.step(frameDelta());
  pitScene.update(task, EMPTY);
}
const EMPTY: ReadonlySet<string> = new Set();

function frameDelta(): number {
  const now = performance.now();
  const dt = lastFrameMs === undefined ? 1 / 72 : (now - lastFrameMs) / 1000;
  lastFrameMs = now;
  // A backgrounded tab returns a multi-second delta, which would teleport
  // every ball through the floor in one step.
  return Math.min(dt, 0.05);
}

function xrFrame(_time: number, frame?: XRFrame): void {
  const dtSeconds = frameDelta();
  const xrSession = renderer.xr.getSession();

  lastHands =
    frame && xrReferenceSpace && xrSession
      ? readBothHands(frame, xrSession, xrReferenceSpace)
      : { left: null, right: null };

  shadowHands.update(lastHands);
  hud.place(renderer.xr.getCamera());

  const pressed = hud.update(fingertipOf(lastHands.right) ?? fingertipOf(lastHands.left));
  if (pressed === "reset") resetPit();

  const update = session.update({
    left: toHandInput(lastHands.left),
    right: toHandInput(lastHands.right),
    dtSeconds,
  });

  task.step(dtSeconds);
  pitScene.update(task, update.reachable);
  renderScore();

  renderer.render(scene, camera);
}

/** WebXR-space hand -> the struct_world input the interaction works in.
 * Conversion happens here, once, for the same reason it does on the teleop
 * path: everything downstream reasons in one frame. */
function toHandInput(hand: HandFrame | null): HandInput | null {
  if (!hand) return null;
  // The grab point is the palm when the runtime reports it, else the wrist --
  // for a ball you close a whole hand around, that is far closer to where the
  // ball should sit than a fingertip is.
  const anchor = hand.joints["palm"] ?? hand.joints["wrist"];
  if (!anchor) return null;
  return {
    position: webxrToStruct.position(anchor.position) as Vec3,
    orientation: webxrToStruct.quaternion(anchor.orientation),
    gripper: hand.gripper,
  };
}

function fingertipOf(hand: HandFrame | null): Vec3 | null {
  return (hand?.joints["index-finger-tip"]?.position as Vec3 | undefined) ?? null;
}

renderScore();
pitScene.update(task, EMPTY);
