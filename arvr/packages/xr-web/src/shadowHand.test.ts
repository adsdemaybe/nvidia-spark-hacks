import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { HAND_JOINTS, PALM_JOINT, type HandFrame, type JointPose } from "./hands";
import { HAND_BONES, ShadowHand, ShadowHands, resolveBoneSegments } from "./shadowHand";

const ALL_JOINT_NAMES = new Set<string>([...HAND_JOINTS, PALM_JOINT]);

const joint = (p: [number, number, number]): JointPose => ({
  position: p,
  orientation: [0, 0, 0, 1],
  radius: null,
});

function frame(handedness: "left" | "right", joints: Record<string, JointPose>): HandFrame {
  return { handedness, joints, pinchApertureM: null, gripper: 0 };
}

describe("HAND_BONES", () => {
  it("only references valid canonical joint names", () => {
    for (const [from, to] of HAND_BONES) {
      expect(ALL_JOINT_NAMES.has(from)).toBe(true);
      expect(ALL_JOINT_NAMES.has(to)).toBe(true);
    }
  });

  it("has no duplicate segments", () => {
    const seen = new Set(HAND_BONES.map(([a, b]) => `${a}->${b}`));
    expect(seen.size).toBe(HAND_BONES.length);
  });

  it("every non-wrist joint is reachable from the wrist", () => {
    const reachable = new Set(["wrist"]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const [from, to] of HAND_BONES) {
        if (reachable.has(from) && !reachable.has(to)) {
          reachable.add(to);
          changed = true;
        }
      }
    }
    for (const name of ALL_JOINT_NAMES) {
      expect(reachable.has(name)).toBe(true);
    }
  });
});

describe("resolveBoneSegments", () => {
  it("returns nothing for an untracked (null) hand", () => {
    expect(resolveBoneSegments(null)).toEqual([]);
  });

  it("omits segments touching an untracked joint, never fabricates one", () => {
    const hand = frame("right", { wrist: joint([0, 0, 0]) });
    const segments = resolveBoneSegments(hand);
    expect(segments).toEqual([]);
    for (const seg of segments) {
      expect(Number.isFinite(seg.from[0])).toBe(true);
    }
  });

  it("resolves a segment when both endpoints are tracked", () => {
    const hand = frame("right", {
      wrist: joint([0, 0, 0]),
      "thumb-metacarpal": joint([0.01, 0, 0]),
    });
    const segments = resolveBoneSegments(hand);
    expect(segments).toHaveLength(1);
    expect(segments[0]).toEqual({ from: [0, 0, 0], to: [0.01, 0, 0] });
  });

  it("topology doesn't depend on handedness", () => {
    const joints: Record<string, JointPose> = {};
    for (const name of ALL_JOINT_NAMES) joints[name] = joint([0, 0, 0]);
    const left = resolveBoneSegments(frame("left", joints));
    const right = resolveBoneSegments(frame("right", joints));
    expect(left.length).toBe(right.length);
    expect(left.length).toBe(HAND_BONES.length);
  });

  it("no NaNs when fed a sequence of frames with varying tracked joints", () => {
    const sequences: HandFrame[] = [
      frame("right", { wrist: joint([0.3, 0.1, 0.5]) }),
      frame("right", {
        wrist: joint([0.31, 0.1, 0.5]),
        "thumb-tip": joint([0.32, 0.1, 0.48]),
        "index-finger-tip": joint([0.30, 0.1, 0.48]),
      }),
      frame("right", { wrist: joint([0.32, 0.1, 0.5]) }),
    ];
    for (const f of sequences) {
      for (const seg of resolveBoneSegments(f)) {
        for (const coord of [...seg.from, ...seg.to]) {
          expect(Number.isFinite(coord)).toBe(true);
        }
      }
    }
  });
});

// Reaching into the private joint mesh map, the same way shadowRobot.test.ts
// reaches into its joint Groups: there is no public per-joint getter, and
// the rendered position of a joint is the only evidence that distinguishes
// "the left hand was drawn" from "a hand was drawn in the left slot".
function wristMeshX(shadow: ShadowHand): number {
  const joints = (shadow as unknown as { joints: Map<string, THREE.Mesh> }).joints;
  const wrist = joints.get("wrist");
  if (!wrist) throw new Error("expected a wrist mesh");
  return wrist.position.x;
}

const LEFT_X = -0.25;
const RIGHT_X = 0.25;

/** A hand whose every joint sits at a handedness-identifying X. */
function handAt(handedness: "left" | "right", x: number): HandFrame {
  const joints: Record<string, JointPose> = {};
  for (const name of ALL_JOINT_NAMES) joints[name] = joint([x, 1.2, -0.4]);
  return frame(handedness, joints);
}

describe("ShadowHand (single-hand API, unchanged)", () => {
  it("still hides itself on an untracked hand rather than freezing stale joints", () => {
    const shadow = new ShadowHand();
    shadow.update(handAt("right", RIGHT_X));
    expect(shadow.root.visible).toBe(true);
    shadow.update(null);
    expect(shadow.root.visible).toBe(false);
  });
});

describe("ShadowHands", () => {
  it("parents both hands under one root, so a caller adds one object to the scene", () => {
    const hands = new ShadowHands();
    expect(hands.root.children).toContain(hands.left.root);
    expect(hands.root.children).toContain(hands.right.root);
  });

  it("shows both hands when both are tracked", () => {
    const hands = new ShadowHands();
    hands.update({ left: handAt("left", LEFT_X), right: handAt("right", RIGHT_X) });

    expect(hands.left.root.visible).toBe(true);
    expect(hands.right.root.visible).toBe(true);
  });

  it("does not swap left and right", () => {
    // The rendering-side twin of readBothHands's swap test. A swap here
    // looks entirely correct on screen until the demo asks the left hand to
    // do something and the right one moves, so it must be caught by
    // position, not by the visible/hidden flags.
    const hands = new ShadowHands();
    hands.update({ left: handAt("left", LEFT_X), right: handAt("right", RIGHT_X) });

    expect(wristMeshX(hands.left)).toBe(LEFT_X);
    expect(wristMeshX(hands.right)).toBe(RIGHT_X);
  });

  it("hides only the right hand when just the left is tracked", () => {
    const hands = new ShadowHands();
    hands.update({ left: handAt("left", LEFT_X), right: null });

    expect(hands.left.root.visible).toBe(true);
    expect(wristMeshX(hands.left)).toBe(LEFT_X);
    expect(hands.right.root.visible).toBe(false);
  });

  it("hides only the left hand when just the right is tracked", () => {
    const hands = new ShadowHands();
    hands.update({ left: null, right: handAt("right", RIGHT_X) });

    expect(hands.right.root.visible).toBe(true);
    expect(wristMeshX(hands.right)).toBe(RIGHT_X);
    expect(hands.left.root.visible).toBe(false);
  });

  it("hides both when neither hand is tracked", () => {
    const hands = new ShadowHands();
    hands.update({ left: null, right: null });

    expect(hands.left.root.visible).toBe(false);
    expect(hands.right.root.visible).toBe(false);
  });

  it("brings a dropped hand back without disturbing the one that stayed", () => {
    // Hands cross the tracking volume's edge constantly in a room-scale
    // demo, so losing and regaining one is the steady state, not an edge
    // case -- and the hand that never dropped must not flicker with it.
    const hands = new ShadowHands();
    hands.update({ left: handAt("left", LEFT_X), right: handAt("right", RIGHT_X) });
    hands.update({ left: null, right: handAt("right", RIGHT_X) });

    expect(hands.right.root.visible).toBe(true);
    expect(wristMeshX(hands.right)).toBe(RIGHT_X);

    hands.update({ left: handAt("left", LEFT_X), right: handAt("right", RIGHT_X) });
    expect(hands.left.root.visible).toBe(true);
    expect(wristMeshX(hands.left)).toBe(LEFT_X);
    expect(wristMeshX(hands.right)).toBe(RIGHT_X);
  });

  it("gives each hand its own material, so one hand's color cannot bleed into the other", () => {
    // ShadowHand recolors its shared material per handedness on every
    // update. Two instances is what keeps that per-hand; a shared material
    // would leave both hands whichever color updated last.
    const hands = new ShadowHands();
    hands.update({ left: handAt("left", LEFT_X), right: handAt("right", RIGHT_X) });

    const materialOf = (shadow: ShadowHand): THREE.MeshStandardMaterial =>
      (shadow as unknown as { material: THREE.MeshStandardMaterial }).material;

    expect(materialOf(hands.left)).not.toBe(materialOf(hands.right));
    expect(materialOf(hands.left).color.getHex()).not.toBe(
      materialOf(hands.right).color.getHex(),
    );
  });
});
