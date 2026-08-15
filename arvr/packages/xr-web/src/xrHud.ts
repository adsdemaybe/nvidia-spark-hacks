/**
 * The HUD that exists inside the headset.
 *
 * Spatial Teach's controls and readout are DOM (`spatial-teach.html`), which
 * is the right choice on a desktop and completely invisible in an immersive
 * session: `immersive-vr` composites nothing but the WebGL layer, and
 * `immersive-ar` only shows DOM when the runtime grants `dom-overlay`. Before
 * this module, entering XR at START DEMO meant the human could no longer
 * reach CALIBRATE or FINISH -- the demo could be started from a headset and
 * not ended from one.
 *
 * So the controls are drawn in the scene and pressed by poking them with an
 * index fingertip. Poking, not a pinch-and-ray: the hand is already the input
 * device being demonstrated with, rays need a second gesture to click, and a
 * fingertip position is the one signal hand tracking reports most reliably.
 * `PokeTracker` holds the press semantics (edge-triggered, hysteretic) and is
 * plain geometry so it can be tested without a headset or a GPU; `XrHud`
 * holds the three.js objects.
 */

import * as THREE from "three";
import type { Vec3 } from "./contracts";

export interface PokeTarget {
  id: string;
  /** Panel-local center, meters. */
  center: Vec3;
  halfExtents: Vec3;
}

/** How far past a button face the fingertip must travel before the button is
 * armed again. Hand tracking jitters by millimeters; without this a fingertip
 * resting on an edge reads as a stream of presses. */
export const DEFAULT_EXIT_MARGIN_M = 0.015;

export class PokeTracker {
  private latched: string | null = null;
  private inside: string | null = null;

  constructor(
    private targets: PokeTarget[],
    private readonly exitMarginM = DEFAULT_EXIT_MARGIN_M,
  ) {}

  /** What the fingertip is currently in, pressed or not. */
  get hovered(): string | null {
    return this.inside;
  }

  /** Rebuilding the row (the flow advanced) drops any held latch with it. */
  setTargets(targets: PokeTarget[]): void {
    this.targets = targets;
    this.latched = null;
    this.inside = null;
  }

  /**
   * Advance one frame. Returns the id of a button pressed *this* frame, or
   * null. Edge-triggered on entry, so holding a finger in a button presses it
   * once -- a level-triggered version would fire ~72 times a second.
   */
  update(fingertipLocal: Vec3 | null): string | null {
    if (!fingertipLocal) {
      this.latched = null;
      this.inside = null;
      return null;
    }

    if (this.latched) {
      const held = this.targets.find((t) => t.id === this.latched);
      // Still latched until the finger clears the button plus its margin.
      if (held && contains(held, fingertipLocal, this.exitMarginM)) {
        this.inside = contains(held, fingertipLocal, 0) ? held.id : null;
        return null;
      }
      this.latched = null;
    }

    const hit = this.targets.find((t) => contains(t, fingertipLocal, 0));
    this.inside = hit?.id ?? null;
    if (!hit) return null;

    this.latched = hit.id;
    return hit.id;
  }
}

function contains(target: PokeTarget, point: Vec3, marginM: number): boolean {
  for (let axis = 0; axis < 3; axis += 1) {
    const delta = Math.abs(point[axis]! - target.center[axis]!);
    if (delta > target.halfExtents[axis]! + marginM) return false;
  }
  return true;
}

export interface ButtonLayout {
  width: number;
  height: number;
  gap: number;
}

/** Minimum half-depth for a poke target. A fingertip travels several
 * centimeters between hand-tracking samples, so a visually-thin button still
 * needs a thick collision volume or fast pokes pass straight through it. */
export const POKE_HALF_DEPTH_M = 0.02;

/** Lay a row of buttons out symmetrically about the panel's local origin. */
export function layoutButtons(ids: string[], layout: ButtonLayout): PokeTarget[] {
  const pitch = layout.width + layout.gap;
  const offset = ((ids.length - 1) * pitch) / 2;
  return ids.map((id, i) => ({
    id,
    center: [i * pitch - offset, 0, 0] as Vec3,
    halfExtents: [layout.width / 2, layout.height / 2, POKE_HALF_DEPTH_M] as Vec3,
  }));
}

// --------------------------------------------------------------- rendering --

const PANEL_WIDTH_M = 0.42;
const PANEL_HEIGHT_M = 0.26;
const CANVAS_SCALE = 512 / PANEL_WIDTH_M;
const BUTTON_LAYOUT: ButtonLayout = { width: 0.12, height: 0.05, gap: 0.02 };
/** Where the panel is planted when a session starts, relative to the head:
 * chest height, just under half a meter out. Close enough to poke without
 * leaning, low enough not to sit over the workspace. */
export const PANEL_OFFSET_M: Vec3 = [0, -0.25, -0.45];

export interface XrHudButton {
  id: string;
  label: string;
}

/**
 * The in-world panel: a readout board with a row of poke buttons under it.
 *
 * World-locked once placed rather than head-locked -- a panel that follows the
 * head is impossible to poke accurately, because it retreats from the finger.
 */
export class XrHud {
  readonly root = new THREE.Group();
  private readonly tracker = new PokeTracker([]);
  private readonly canvas: HTMLCanvasElement | undefined;
  private readonly ctx: CanvasRenderingContext2D | null = null;
  private readonly texture: THREE.CanvasTexture | undefined;
  private readonly buttonGroup = new THREE.Group();
  private buttons: XrHudButton[] = [];
  private targets: PokeTarget[] = [];
  private text = "";
  private placed = false;

  constructor() {
    this.root.visible = false;

    // A canvas only exists in a browser. Guarded so importing this module in
    // a node test (or any headless context) is not a crash -- the geometry
    // half is what the tests exercise.
    if (typeof document !== "undefined") {
      this.canvas = document.createElement("canvas");
      this.canvas.width = Math.round(PANEL_WIDTH_M * CANVAS_SCALE);
      this.canvas.height = Math.round(PANEL_HEIGHT_M * CANVAS_SCALE);
      this.ctx = this.canvas.getContext("2d");
      this.texture = new THREE.CanvasTexture(this.canvas);
    }

    const board = new THREE.Mesh(
      new THREE.PlaneGeometry(PANEL_WIDTH_M, PANEL_HEIGHT_M),
      new THREE.MeshBasicMaterial({
        ...(this.texture ? { map: this.texture } : { color: 0x14171c }),
        transparent: true,
        opacity: 0.92,
        depthTest: false,
      }),
    );
    board.renderOrder = 10;
    this.root.add(board);

    this.buttonGroup.position.set(0, -PANEL_HEIGHT_M / 2 - BUTTON_LAYOUT.height, 0.01);
    this.root.add(this.buttonGroup);
  }

  /**
   * Plant the panel in the room, once, in front of wherever the head is now.
   * Repeated calls are ignored: re-planting mid-session would move a button
   * out from under a finger already reaching for it.
   */
  place(camera: THREE.Camera): void {
    if (this.placed) return;
    this.placed = true;
    this.root.visible = true;

    const headPosition = new THREE.Vector3();
    const headQuaternion = new THREE.Quaternion();
    camera.getWorldPosition(headPosition);
    camera.getWorldQuaternion(headQuaternion);

    // Yaw only: the panel should stand upright even if the human is looking
    // at the floor when the session starts.
    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(headQuaternion);
    const yaw = Math.atan2(forward.x, forward.z);
    const offset = new THREE.Vector3(...PANEL_OFFSET_M).applyEuler(
      new THREE.Euler(0, yaw, 0),
    );

    this.root.position.copy(headPosition).add(offset);
    this.root.rotation.set(0, yaw, 0);
  }

  /** Forget the placement so the next session plants a fresh panel. */
  unplace(): void {
    this.placed = false;
    this.root.visible = false;
  }

  get isPlaced(): boolean {
    return this.placed;
  }

  setButtons(buttons: XrHudButton[]): void {
    const unchanged =
      buttons.length === this.buttons.length &&
      buttons.every((b, i) => b.id === this.buttons[i]?.id && b.label === this.buttons[i]?.label);
    if (unchanged) return;

    this.buttons = buttons;
    this.targets = layoutButtons(
      buttons.map((b) => b.id),
      BUTTON_LAYOUT,
    );
    this.tracker.setTargets(this.targets);
    this.buttonGroup.clear();

    for (const [i, button] of buttons.entries()) {
      const target = this.targets[i]!;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(BUTTON_LAYOUT.width, BUTTON_LAYOUT.height, 0.012),
        new THREE.MeshBasicMaterial({ color: 0x2c5f8a, depthTest: false }),
      );
      mesh.name = button.id;
      mesh.renderOrder = 11;
      mesh.position.set(target.center[0], target.center[1], target.center[2]);
      this.buttonGroup.add(mesh);
      this.buttonGroup.add(makeLabel(button.label, target.center));
    }
  }

  setText(text: string): void {
    if (text === this.text) return;
    this.text = text;
    this.redraw();
  }

  /**
   * Feed one frame's fingertip (world space) and get back the id of any button
   * pressed. Null when nothing was pressed, including when the hand is lost.
   */
  update(fingertipWorld: Vec3 | null): string | null {
    if (!this.placed) return null;
    let local: Vec3 | null = null;
    if (fingertipWorld) {
      const point = new THREE.Vector3(...fingertipWorld);
      this.buttonGroup.worldToLocal(point);
      local = [point.x, point.y, point.z];
    }
    const pressed = this.tracker.update(local);
    this.highlight(this.tracker.hovered);
    return pressed;
  }

  private highlight(hoveredId: string | null): void {
    for (const child of this.buttonGroup.children) {
      if (!(child instanceof THREE.Mesh)) continue;
      const material = child.material as THREE.MeshBasicMaterial;
      if (!material.color) continue;
      material.color.setHex(child.name === hoveredId ? 0x61afef : 0x2c5f8a);
    }
  }

  private redraw(): void {
    const ctx = this.ctx;
    if (!ctx || !this.canvas) return;
    ctx.fillStyle = "#14171c";
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.fillStyle = "#abb2bf";
    ctx.font = "20px ui-monospace, monospace";
    ctx.textBaseline = "top";
    const lines = this.text.split("\n");
    for (const [i, line] of lines.entries()) {
      ctx.fillText(line, 16, 16 + i * 24);
    }
    if (this.texture) this.texture.needsUpdate = true;
  }
}

/** A button's caption, drawn to its own small canvas. Falls back to an
 * unlabeled plate in a headless context rather than throwing. */
function makeLabel(text: string, center: Vec3): THREE.Object3D {
  const group = new THREE.Group();
  group.position.set(center[0], center[1], center[2] + 0.008);
  if (typeof document === "undefined") return group;

  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 96;
  const ctx = canvas.getContext("2d");
  if (!ctx) return group;
  ctx.fillStyle = "rgba(0,0,0,0)";
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 36px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);

  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(BUTTON_LAYOUT.width * 0.92, BUTTON_LAYOUT.height * 0.72),
    new THREE.MeshBasicMaterial({
      map: new THREE.CanvasTexture(canvas),
      transparent: true,
      depthTest: false,
    }),
  );
  mesh.renderOrder = 12;
  group.add(mesh);
  return group;
}
