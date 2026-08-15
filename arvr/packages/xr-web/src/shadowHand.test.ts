import { describe, expect, it } from "vitest";
import { HAND_JOINTS, PALM_JOINT, type HandFrame, type JointPose } from "./hands";
import { HAND_BONES, resolveBoneSegments } from "./shadowHand";

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
