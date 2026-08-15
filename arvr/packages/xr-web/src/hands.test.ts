import { describe, expect, it } from "vitest";
import {
  HAND_JOINTS,
  PINCH_CLOSED_M,
  PINCH_OPEN_M,
  distance,
  gripperFromAperture,
  wristTarget,
  type HandFrame,
  type JointPose,
} from "./hands";

const joint = (p: [number, number, number]): JointPose => ({
  position: p,
  orientation: [0, 0, 0, 1],
  radius: null,
});

describe("HAND_JOINTS", () => {
  it("covers the 25 WebXR-named joints", () => {
    expect(HAND_JOINTS).toHaveLength(25);
    expect(HAND_JOINTS).toContain("wrist");
    expect(HAND_JOINTS).toContain("thumb-tip");
    expect(HAND_JOINTS).toContain("index-finger-tip");
  });

  it("has no duplicates", () => {
    expect(new Set(HAND_JOINTS).size).toBe(HAND_JOINTS.length);
  });
});

describe("gripperFromAperture", () => {
  it("reads fully closed when the tips are touching", () => {
    expect(gripperFromAperture(PINCH_CLOSED_M)).toBe(1);
  });

  it("reads fully open at a relaxed pinch", () => {
    expect(gripperFromAperture(PINCH_OPEN_M)).toBe(0);
  });

  it("saturates rather than asking for a grip the robot cannot make", () => {
    expect(gripperFromAperture(0.001)).toBe(1);
    expect(gripperFromAperture(0.5)).toBe(0);
  });

  it("is monotonic between the two", () => {
    const mid = gripperFromAperture((PINCH_CLOSED_M + PINCH_OPEN_M) / 2);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
  });

  it("treats an untracked pinch as open, not as a grab", () => {
    // A missing hand must never close the gripper -- that would grasp on a
    // tracking dropout.
    expect(gripperFromAperture(null)).toBe(0);
    expect(gripperFromAperture(NaN)).toBe(0);
  });
});

describe("distance", () => {
  it("measures between joint positions", () => {
    expect(distance(joint([0, 0, 0]), joint([0, 0, 0.03]))).toBeCloseTo(0.03, 12);
  });
});

describe("wristTarget", () => {
  it("returns the wrist pose, which is the 6-DoF end-effector target", () => {
    const hand: HandFrame = {
      handedness: "right",
      joints: { wrist: joint([0.1, 1.2, -0.3]) },
      pinchApertureM: null,
      gripper: 0,
    };

    expect(wristTarget(hand)?.position).toEqual([0.1, 1.2, -0.3]);
  });

  it("returns null when the wrist was not tracked", () => {
    const hand: HandFrame = {
      handedness: "left",
      joints: {},
      pinchApertureM: null,
      gripper: 0,
    };

    expect(wristTarget(hand)).toBeNull();
  });
});
