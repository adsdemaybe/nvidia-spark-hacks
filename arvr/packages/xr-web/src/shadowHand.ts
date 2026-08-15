/**
 * ShadowHand — Shadow Robot Spatial Demonstration Pipeline spec sections
 * 19-20, Phase 5. What the human sees to know STRUCT is actually tracking
 * their hand correctly, before any retargeting happens. Not training data
 * itself — a visualization of the canonical HandFrame (hands.ts), nothing
 * more.
 *
 * Renders directly in WebXR space with NO coordinate conversion —
 * hands.ts's `HandFrame` (from a real `readHand()` call, or from
 * mockHand.ts's already-converted mock frames) is already WebXR-native.
 * Calling `structToWebxr` here would double-convert and silently point the
 * shadow hand in the wrong direction — the same failure mode adapter.ts's
 * own docstring warns about, just triggered from the opposite side.
 */

import * as THREE from "three";
import { HAND_JOINTS, PALM_JOINT, type HandFrame, type HandPair } from "./hands";

/** Parent-chain adjacency, one entry per bone segment. Built explicitly
 * rather than derived from HAND_JOINTS's ordering, so the topology is
 * something a reader can check by eye against WebXR's joint naming, not
 * something that silently breaks if that ordering ever changes. */
export const HAND_BONES: ReadonlyArray<readonly [string, string]> = [
  ["wrist", "thumb-metacarpal"],
  ["thumb-metacarpal", "thumb-phalanx-proximal"],
  ["thumb-phalanx-proximal", "thumb-phalanx-distal"],
  ["thumb-phalanx-distal", "thumb-tip"],

  ["wrist", "index-finger-metacarpal"],
  ["index-finger-metacarpal", "index-finger-phalanx-proximal"],
  ["index-finger-phalanx-proximal", "index-finger-phalanx-intermediate"],
  ["index-finger-phalanx-intermediate", "index-finger-phalanx-distal"],
  ["index-finger-phalanx-distal", "index-finger-tip"],

  ["wrist", "middle-finger-metacarpal"],
  ["middle-finger-metacarpal", "middle-finger-phalanx-proximal"],
  ["middle-finger-phalanx-proximal", "middle-finger-phalanx-intermediate"],
  ["middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal"],
  ["middle-finger-phalanx-distal", "middle-finger-tip"],

  ["wrist", "ring-finger-metacarpal"],
  ["ring-finger-metacarpal", "ring-finger-phalanx-proximal"],
  ["ring-finger-phalanx-proximal", "ring-finger-phalanx-intermediate"],
  ["ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal"],
  ["ring-finger-phalanx-distal", "ring-finger-tip"],

  ["wrist", "pinky-finger-metacarpal"],
  ["pinky-finger-metacarpal", "pinky-finger-phalanx-proximal"],
  ["pinky-finger-phalanx-proximal", "pinky-finger-phalanx-intermediate"],
  ["pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal"],
  ["pinky-finger-phalanx-distal", "pinky-finger-tip"],

  ["wrist", "palm"],
] as const;

export interface BoneSegment {
  from: readonly [number, number, number];
  to: readonly [number, number, number];
}

/** Pure math, no THREE dependency — testable without a WebGL context, same
 * split as arm.ts's reachStatus. Segments touching an untracked joint are
 * simply omitted, never rendered from a fabricated [0,0,0]. */
export function resolveBoneSegments(hand: HandFrame | null): BoneSegment[] {
  if (!hand) return [];
  const segments: BoneSegment[] = [];
  for (const [from, to] of HAND_BONES) {
    const a = hand.joints[from];
    const b = hand.joints[to];
    if (!a || !b) continue;
    segments.push({ from: a.position, to: b.position });
  }
  return segments;
}

const LEFT_COLOR = 0x4fc3f7;
const RIGHT_COLOR = 0xf48fb1;
const JOINT_RADIUS_M = 0.01;
const OPACITY = 0.4;
const ALL_JOINT_NAMES: readonly string[] = [...HAND_JOINTS, PALM_JOINT];

export class ShadowHand {
  readonly root = new THREE.Group();

  private readonly joints = new Map<string, THREE.Mesh>();
  private readonly bones: THREE.LineSegments;
  private readonly bonePositions: Float32Array;
  private material = new THREE.MeshStandardMaterial({
    color: RIGHT_COLOR,
    transparent: true,
    opacity: OPACITY,
    depthWrite: false,
  });

  constructor() {
    const sphereGeometry = new THREE.SphereGeometry(JOINT_RADIUS_M, 12, 8);
    for (const name of ALL_JOINT_NAMES) {
      const mesh = new THREE.Mesh(sphereGeometry, this.material);
      mesh.visible = false;
      this.joints.set(name, mesh);
      this.root.add(mesh);
    }

    this.bonePositions = new Float32Array(HAND_BONES.length * 2 * 3);
    const boneGeometry = new THREE.BufferGeometry();
    boneGeometry.setAttribute("position", new THREE.BufferAttribute(this.bonePositions, 3));
    boneGeometry.setDrawRange(0, 0);
    this.bones = new THREE.LineSegments(
      boneGeometry,
      new THREE.LineBasicMaterial({ color: RIGHT_COLOR, transparent: true, opacity: OPACITY }),
    );
    this.bones.frustumCulled = false;
    this.root.add(this.bones);
  }

  /** null = hand not currently tracked; hides the whole group rather than
   * leaving stale joints frozen in their last known pose. */
  update(hand: HandFrame | null): void {
    this.root.visible = hand !== null;
    if (!hand) return;

    const color = hand.handedness === "left" ? LEFT_COLOR : RIGHT_COLOR;
    this.material.color.setHex(color);
    (this.bones.material as THREE.LineBasicMaterial).color.setHex(color);

    for (const [name, mesh] of this.joints) {
      const joint = hand.joints[name];
      mesh.visible = joint !== undefined;
      if (joint) mesh.position.set(...joint.position);
    }

    const segments = resolveBoneSegments(hand);
    let i = 0;
    for (const segment of segments) {
      this.bonePositions.set(segment.from, i * 6);
      this.bonePositions.set(segment.to, i * 6 + 3);
      i += 1;
    }
    const attribute = this.bones.geometry.getAttribute("position") as THREE.BufferAttribute;
    attribute.needsUpdate = true;
    this.bones.geometry.setDrawRange(0, segments.length * 2);
  }
}

/**
 * Both shadow hands, for the room-scale demo where the human manipulates
 * with two hands rather than teleoperating one arm.
 *
 * Composed of two independent `ShadowHand`s rather than generalized into one
 * class that draws N hands, because everything a single hand owns -- its
 * joint meshes, its bone buffer, the material whose color says which hand
 * this is -- is per-hand state that a shared implementation would have to
 * index by handedness anyway. Two instances get that separation from the
 * language for free, and leave `ShadowHand` exactly as the teleop pages
 * (spatialTeachMain.ts, canPickupMain.ts) already use it.
 */
export class ShadowHands {
  readonly root = new THREE.Group();
  readonly left = new ShadowHand();
  readonly right = new ShadowHand();

  constructor() {
    this.root.add(this.left.root);
    this.root.add(this.right.root);
  }

  /**
   * Show this frame's hands. A null slot hides that hand alone, so one hand
   * leaving the tracking volume never blanks the other -- the failure a
   * single visibility flag over both hands would produce, and the one a
   * two-handed demo hits constantly as hands cross the FoV edge.
   *
   * The pair is passed straight through with each slot going to the matching
   * instance and no re-inspection of `handedness`. `readBothHands` already
   * guarantees slot and frame agree, so re-deriving the routing here would
   * add a second place for the two to drift apart rather than a safeguard.
   */
  update(hands: HandPair): void {
    this.left.update(hands.left);
    this.right.update(hands.right);
  }
}
